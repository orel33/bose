#!/usr/bin/env python3
"""Diagnostics Bose, sauvegardes, lecture et redémarrage sans dépendances Python externes."""
import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import socket
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPEAKERS = {
    'veranda': '192.168.0.152',
    'cuisine': '192.168.0.151',
    'chambre': '192.168.0.153',
}
HOSTS = tuple(SPEAKERS.values())
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def resolve_host(value):
    """Accepter les noms des enceintes et les anciennes adresses IP."""
    host = SPEAKERS.get(value.casefold(), value)
    if host not in HOSTS:
        raise argparse.ArgumentTypeError('enceinte inconnue : choisir veranda, cuisine ou chambre')
    return host


def speaker_name(host):
    return next((name.capitalize() for name, address in SPEAKERS.items() if address == host), host)


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
            print(f'{speaker_name(host)}: sauvegarde XML complète')
        except Exception as exc:
            errors.append(host)
            print(f'{speaker_name(host)}: sauvegarde incomplète ({exc})', file=sys.stderr)
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
    print(f'{speaker_name(host)}: slots modifiés = {changed or "aucun"}')
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
    print(f'{speaker_name(host)}: restauration des seuls slots 1 et 2 ; apply={apply}')
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


def reboot(host):
    """Envoyer sys reboot à la console de diagnostic TCP, sans SSH."""
    with socket.create_connection((host, 17000), timeout=6) as connection:
        deadline = time.monotonic() + 6
        banner = bytearray()
        # Attendre l’invite, y compris si elle arrive en plusieurs paquets.
        while not banner.rstrip().endswith(b'->'):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(banner) >= 4096:
                raise RuntimeError('Invite de diagnostic -> absente ; aucune commande envoyée.')
            connection.settimeout(remaining)
            try:
                chunk = connection.recv(1024)
            except socket.timeout as exc:
                raise RuntimeError('Invite de diagnostic -> absente ; aucune commande envoyée.') from exc
            if not chunk:
                raise RuntimeError('Console fermée avant son invite ; aucune commande envoyée.')
            banner.extend(chunk)
        connection.settimeout(6)
        connection.sendall(b'sys reboot\r\n')
    print(f'{speaker_name(host)}: commande sys reboot envoyée sur le port 17000.')
    print('Attendre environ une minute, puis vérifier avec la commande status. '
          'Le retour de l’enceinte n’est pas vérifié automatiquement.')


def status(host):
    info = request(host, 'info')[1]
    playing = request(host, 'now_playing')[1]
    presets = slots(request(host, 'presets')[1])
    print(f'{speaker_name(host)}: {info.findtext("name")} ; source={playing.get("source")} ; état={playing.findtext("playStatus")}')
    print('Presets :', ', '.join(f'{n}={c.get("source")}' for n, c in presets.items()))


def zone(master, action, members):
    """Basculer un groupe natif limité au maître et aux membres indiqués."""
    members = tuple(members)
    if action not in ('join', 'leave', 'toggle') or not members or master in members:
        raise ValueError('Configuration du groupe invalide')
    hosts = (master, *members)
    if len(set(hosts)) != len(hosts):
        raise ValueError('Enceinte dupliquée dans le groupe')
    identities = {host: request(host, 'info')[1].get('deviceID') for host in hosts}
    if not all(identities.values()) or len(set(identities.values())) != len(hosts):
        raise ValueError('Identités des enceintes absentes ou dupliquées')
    current = {host: request(host, 'getZone')[1] for host in hosts}
    zone_master = current[master].get('master')
    if zone_master and zone_master != identities[master]:
        raise RuntimeError('Veranda est secondaire dans un autre groupe')
    actual_members = {node.get('ipaddress') for node in current[master].findall('member')}
    if zone_master and actual_members - set(hosts):
        raise RuntimeError('Le groupe contient une autre enceinte ; aucune modification')
    for host in members:
        other_master = current[host].get('master')
        if other_master and other_master != identities[master]:
            raise RuntimeError(f'{host} appartient à un autre groupe')
    if action == 'toggle':
        action = 'leave' if zone_master and set(members) <= actual_members else 'join'
    if action == 'join':
        if zone_master and set(members) <= actual_members:
            return 'join'
        body = ET.Element('zone', master=identities[master])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect((master, 8090))
            body.set('senderIPAddress', connection.getsockname()[0])
        for host in hosts:
            ET.SubElement(body, 'member', ipaddress=host).text = identities[host]
        request(master, 'setZone', ET.tostring(body))
    elif zone_master:
        # Le firmware ST10 retire un seul secondaire par requête.
        for host in members:
            if host in actual_members:
                body = ET.Element('zone', master=identities[master])
                ET.SubElement(body, 'member', ipaddress=host).text = identities[host]
                request(master, 'removeZoneSlave', ET.tostring(body))
    else:
        return 'leave'
    for _ in range(10):
        time.sleep(0.5)
        updated = {host: request(host, 'getZone')[1] for host in hosts}
        if action == 'join':
            complete = (all(root.get('master') == identities[master] for root in updated.values())
                        and {node.get('ipaddress') for node in updated[master].findall('member')} == set(hosts))
        else:
            complete = all(not root.get('master') for root in updated.values())
        if complete:
            return action
    raise RuntimeError('État du groupe non confirmé ; relire /getZone')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default=HOSTS[0], type=resolve_host,
                        choices=HOSTS, metavar='{veranda,cuisine,chambre}',
                        help='Enceinte cible (Veranda par défaut ; les anciennes IP restent acceptées)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    z = sub.add_parser('zone', help='Former, séparer ou consulter un groupe natif Bose')
    z.add_argument('action', choices=('status', 'join', 'leave', 'toggle'))
    z.add_argument('members', nargs='*', type=resolve_host, metavar='enceinte',
                   help='Membres du groupe (Cuisine par défaut avec Veranda)')
    sub.add_parser('probe')
    sub.add_parser('reboot', help='Redémarrer immédiatement la Bose sélectionnée via Telnet (port 17000)')
    b = sub.add_parser('backup')
    b.add_argument('--all', action='store_true')
    p = sub.add_parser('radio')
    p.add_argument('slot', choices=('1', '2', '3', '4', '5'))
    sub.add_parser('play', help='Envoyer Play séparément si nécessaire')
    k = sub.add_parser('key', help='Appui court simulé, sans mémorisation')
    k.add_argument('slot', choices=('1', '2', '3', '4', '5', '6'))
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
    elif args.command == 'zone':
        if args.action == 'status':
            targets = tuple(dict.fromkeys((args.host, *args.members))) if args.members else HOSTS
            names_by_id = {request(host, 'info')[1].get('deviceID'): speaker_name(host)
                           for host in targets}
            for host in targets:
                current = request(host, 'getZone')[1]
                members = [speaker_name(node.get('ipaddress')) for node in current.findall('member')]
                master = current.get('master')
                print(f'{speaker_name(host)}: maître={names_by_id.get(master, master) if master else "aucun"}, membres={members}')
        else:
            members = tuple(args.members)
            if not members and args.host == SPEAKERS['veranda']:
                members = (SPEAKERS['cuisine'],)
            if not members:
                parser.error('indiquer au moins un membre après zone join, leave ou toggle')
            result = zone(args.host, args.action, members)
            names = ' + '.join(speaker_name(host) for host in (args.host, *members))
            print(f'Groupe {names} {"activé" if result == "join" else "désactivé"}.')
    elif args.command == 'reboot':
        reboot(args.host)
    elif args.command == 'probe':
        for port in (8080, 8090, 8091):
            with socket.create_connection((args.host, port), 3):
                print(f'{speaker_name(args.host)} : port {port} accessible')
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
