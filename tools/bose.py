#!/usr/bin/env python3
"""Diagnostics Bose, sauvegardes et lecture sans dépendances Python externes."""
import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import socket
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
HOSTS = ('192.168.0.152', '192.168.0.151', '192.168.0.111')
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(host, endpoint, body=None, port=8090, headers=None):
    req = urllib.request.Request(f'http://{host}:{port}/{endpoint}', data=body,
                                 headers=headers or {'Content-Type': 'application/xml'})
    with HTTP.open(req, timeout=6) as response:
        raw = response.read()
    return raw, ET.fromstring(raw)


def save(path, raw):
    with path.open('xb') as output:
        path.chmod(0o600)
        output.write(raw)


def backup(hosts):
    directory = ROOT / 'backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    directory.mkdir(parents=True, mode=0o700)
    errors = []
    for host in hosts:
        try:
            for endpoint in ('info', 'presets', 'sources', 'now_playing'):
                raw, _ = request(host, endpoint)
                save(directory / f'{endpoint}-{host}.xml', raw)
            print(f'{host}: sauvegarde XML complète')
        except Exception as exc:
            errors.append(host)
            print(f'{host}: sauvegarde incomplète ({exc})', file=sys.stderr)
    save(directory / 'manifest.json', json.dumps({'hosts': hosts, 'failed': errors}).encode())
    print(directory)
    if errors:
        raise RuntimeError('Sauvegarde incomplète : aucune écriture de presets autorisée par cet outil.')


def slots(root):
    result = {}
    for preset in root.findall('preset'):
        slot = preset.get('id')
        if slot not in {str(n) for n in range(1, 7)} or slot in result:
            raise ValueError('Identifiant de preset invalide ou dupliqué')
        content = preset.find('ContentItem')
        if content is None:
            raise ValueError('ContentItem manquant')
        result[slot] = content
    return result


def signature(element):
    if element is None:
        return None
    return (element.tag, sorted(element.attrib.items()), (element.text or '').strip(),
            tuple(signature(child) for child in element))


def compare(host, directory):
    old = slots(ET.parse(directory / f'presets-{host}.xml').getroot())
    current = slots(request(host, 'presets')[1])
    changed = [n for n in sorted(old.keys() | current.keys())
               if signature(old.get(n)) != signature(current.get(n))]
    print(f'{host}: slots modifiés = {changed or "aucun"}')
    if changed:
        raise RuntimeError('Des presets diffèrent de la sauvegarde')


def restore(host, directory, apply):
    # Retour arrière strictement limité à 1/2, jamais 3–6.
    identity = ET.parse(directory / f'info-{host}.xml').getroot().get('deviceID')
    if not identity or request(host, 'info')[1].get('deviceID') != identity:
        raise ValueError('La sauvegarde ne correspond pas à cette enceinte')
    original = slots(ET.parse(directory / f'presets-{host}.xml').getroot())
    before = slots(request(host, 'presets')[1])
    if not all(n in original for n in ('1', '2')):
        raise ValueError('Sauvegarde sans slots 1/2 : restauration automatique refusée')
    print(f'{host}: restauration des seuls slots 1 et 2 ; apply={apply}')
    if not apply:
        return
    backup([host])
    for n in ('1', '2'):
        body = ET.Element('preset', id=n)
        body.append(copy.deepcopy(original[n]))
        request(host, 'storePreset', ET.tostring(body, encoding='utf-8'))
        current = slots(request(host, 'presets')[1])
        if signature(current.get(n)) != signature(original[n]):
            raise RuntimeError(f'Restauration du slot {n} non confirmée')
        if any(signature(before.get(i)) != signature(current.get(i)) for i in ('3', '4', '5', '6')):
            raise RuntimeError('Un slot protégé a changé : arrêt immédiat')
    print('Slots 1/2 restaurés ; slots 3–6 inchangés.')


def soap(host, action, values):
    ns = 'http://schemas.xmlsoap.org/soap/envelope/'
    envelope = ET.Element(f'{{{ns}}}Envelope', {f'{{{ns}}}encodingStyle': 'http://schemas.xmlsoap.org/soap/encoding/'})
    body = ET.SubElement(envelope, f'{{{ns}}}Body')
    command = ET.SubElement(body, f'{{urn:schemas-upnp-org:service:AVTransport:1}}{action}')
    for name, value in values.items():
        ET.SubElement(command, name).text = str(value)
    request(host, 'AVTransport/Control', ET.tostring(envelope), port=8091,
            headers={'Content-Type': 'text/xml; charset="utf-8"',
                     'SOAPAction': f'"urn:schemas-upnp-org:service:AVTransport:1#{action}"'})


def status(host):
    info = request(host, 'info')[1]
    playing = request(host, 'now_playing')[1]
    presets = slots(request(host, 'presets')[1])
    print(f'{host}: {info.findtext("name")} ; source={playing.get("source")} ; état={playing.findtext("playStatus")}')
    print('Presets :', ', '.join(f'{n}={c.get("source")}' for n, c in presets.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default=HOSTS[0], choices=HOSTS)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    sub.add_parser('probe')
    b = sub.add_parser('backup')
    b.add_argument('--all', action='store_true')
    p = sub.add_parser('radio')
    p.add_argument('slot', choices=('1', '2', '3'))
    sub.add_parser('play', help='Envoyer Play séparément si nécessaire')
    k = sub.add_parser('key', help='Appui court simulé, sans mémorisation')
    k.add_argument('slot', choices=('1', '2', '3'))
    for name in ('compare', 'restore'):
        p = sub.add_parser(name)
        p.add_argument('directory', type=Path)
        if name == 'restore':
            p.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.command == 'backup':
        backup(list(HOSTS) if args.all else [args.host])
    elif args.command == 'status':
        status(args.host)
    elif args.command == 'probe':
        for port in (8080, 8090, 8091):
            with socket.create_connection((args.host, port), 3):
                print(f'{args.host}:{port} accessible')
    elif args.command == 'compare':
        compare(args.host, args.directory)
    elif args.command == 'restore':
        restore(args.host, args.directory, args.apply)
    elif args.command == 'radio':
        config = dict(line.split('=', 1) for line in (ROOT / 'config/radios.env').read_text().splitlines()
                      if line and not line.startswith('#'))
        soap(args.host, 'SetAVTransportURI', {'InstanceID': 0, 'CurrentURI': config[f'PRESET_{args.slot}_URL'], 'CurrentURIMetaData': ''})
        print('URI envoyée sans métadonnées. Vérifier le son et la commande status.')
    elif args.command == 'play':
        soap(args.host, 'Play', {'InstanceID': 0, 'Speed': 1})
    elif args.command == 'key':
        request(args.host, 'key', f'<key state="release" sender="Gabbo">PRESET_{args.slot}</key>'.encode())
        print('Appui court simulé envoyé ; aucun preset mémorisé.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Erreur : {exc}', file=sys.stderr)
        sys.exit(1)
