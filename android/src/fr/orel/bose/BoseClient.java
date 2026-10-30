package fr.orel.bose;

import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import javax.xml.parsers.DocumentBuilderFactory;

/** Local SoundTouch API client. No traffic passes through the Raspberry Pi. */
public final class BoseClient {
    public static final class Speaker {
        public final String name;
        public final String host;

        public Speaker(String name, String host) {
            this.name = name;
            this.host = host;
        }
    }

    public static final Speaker VERANDA = new Speaker("Veranda", "192.168.0.152");
    public static final Speaker CUISINE = new Speaker("Cuisine", "192.168.0.151");
    public static final Speaker CHAMBRE = new Speaker("Chambre", "192.168.0.153");
    public static final Speaker[] SPEAKERS = {VERANDA, CUISINE, CHAMBRE};

    public static final class Snapshot {
        public final String name;
        public final String source;
        public final String playStatus;
        public final String title;
        public final int volume;
        public final boolean muted;

        Snapshot(String name, String source, String playStatus, String title,
                 int volume, boolean muted) {
            this.name = name;
            this.source = source;
            this.playStatus = playStatus;
            this.title = title;
            this.volume = volume;
            this.muted = muted;
        }
    }

    public static final class GroupState {
        public final boolean active;
        public final boolean conflict;
        public final Speaker master;

        GroupState(boolean active, boolean conflict, Speaker master) {
            this.active = active;
            this.conflict = conflict;
            this.master = master;
        }
    }

    private static final int TIMEOUT_MS = 6000;
    private static final int MAX_RESPONSE = 1024 * 1024;
    private static final String SOAP_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1";

    private BoseClient() {}

    private static byte[] http(String host, int port, String endpoint, String body,
                               Map<String, String> headers) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(
                "http://" + host + ":" + port + "/" + endpoint).openConnection();
        connection.setConnectTimeout(TIMEOUT_MS);
        connection.setReadTimeout(TIMEOUT_MS);
        connection.setUseCaches(false);
        connection.setInstanceFollowRedirects(false);
        try {
            if (body != null) {
                connection.setRequestMethod("POST");
                connection.setDoOutput(true);
                byte[] data = body.getBytes(StandardCharsets.UTF_8);
                connection.setRequestProperty("Content-Type", "application/xml; charset=utf-8");
                connection.setFixedLengthStreamingMode(data.length);
                if (headers != null) {
                    for (Map.Entry<String, String> item : headers.entrySet()) {
                        connection.setRequestProperty(item.getKey(), item.getValue());
                    }
                }
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(data);
                }
            }
            int status = connection.getResponseCode();
            InputStream stream = status < 400 ? connection.getInputStream() : connection.getErrorStream();
            byte[] answer = stream == null ? new byte[0] : readLimited(stream);
            if (status >= 400) {
                throw new IOException(endpoint + " : HTTP " + status);
            }
            String text = new String(answer, StandardCharsets.UTF_8);
            if (text.contains("<errors") || text.contains("<error>")) {
                throw new IOException(endpoint + " : " + text.replaceAll("<[^>]*>", " ").trim());
            }
            return answer;
        } finally {
            connection.disconnect();
        }
    }

    private static byte[] readLimited(InputStream stream) throws IOException {
        try (InputStream input = stream; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] block = new byte[4096];
            int n;
            while ((n = input.read(block)) != -1) {
                if (output.size() + n > MAX_RESPONSE) {
                    throw new IOException("Réponse Bose trop volumineuse");
                }
                output.write(block, 0, n);
            }
            return output.toByteArray();
        }
    }

    private static Document xml(byte[] bytes) throws Exception {
        String source = new String(bytes, StandardCharsets.UTF_8);
        if (source.contains("<!DOCTYPE") || source.contains("<!ENTITY")) {
            throw new IOException("Réponse XML non prise en charge");
        }
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        try {
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
        } catch (javax.xml.parsers.ParserConfigurationException ignored) {
            // Android XML parsers vary; the explicit DTD check above still applies.
        }
        factory.setExpandEntityReferences(false);
        return factory.newDocumentBuilder().parse(new ByteArrayInputStream(bytes));
    }

    private static Element get(String host, String endpoint) throws Exception {
        return xml(http(host, 8090, endpoint, null, null)).getDocumentElement();
    }

    private static void post(String host, String endpoint, String body) throws Exception {
        http(host, 8090, endpoint, body, null);
    }

    private static String text(Element parent, String tag) {
        NodeList nodes = parent.getElementsByTagName(tag);
        return nodes.getLength() == 0 ? "" : nodes.item(0).getTextContent().trim();
    }

    private static String esc(String value) {
        return value.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&apos;");
    }

    public static Snapshot snapshot(Speaker speaker) throws Exception {
        Element info = get(speaker.host, "info");
        Element playing = get(speaker.host, "now_playing");
        Element volume = get(speaker.host, "volume");
        String title = text(playing, "stationName");
        if (title.isEmpty()) title = text(playing, "itemName");
        if (title.isEmpty()) title = text(playing, "track");
        int level = Integer.parseInt(text(volume, "targetvolume"));
        return new Snapshot(text(info, "name"), playing.getAttribute("source"),
                text(playing, "playStatus"), title, level,
                "true".equalsIgnoreCase(text(volume, "muteenabled")));
    }

    public static void key(Speaker speaker, String name) throws Exception {
        if (!name.matches("[A-Z_0-9]+")) throw new IllegalArgumentException("Touche invalide");
        post(speaker.host, "key", "<key state=\"press\" sender=\"Gabbo\">" + name + "</key>");
        post(speaker.host, "key", "<key state=\"release\" sender=\"Gabbo\">" + name + "</key>");
    }

    public static void setVolume(Speaker speaker, int level) throws Exception {
        if (level < 0 || level > 100) throw new IllegalArgumentException("Volume hors limites");
        post(speaker.host, "volume", "<volume>" + level + "</volume>");
    }

    private static void soap(Speaker speaker, String action, String values) throws Exception {
        String body = "<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" "
                + "s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\">"
                + "<s:Body><u:" + action + " xmlns:u=\"" + SOAP_SERVICE + "\">"
                + values + "</u:" + action + "></s:Body></s:Envelope>";
        Map<String, String> headers = new HashMap<>();
        headers.put("Content-Type", "text/xml; charset=utf-8");
        headers.put("SOAPAction", "\"" + SOAP_SERVICE + "#" + action + "\"");
        http(speaker.host, 8091, "AVTransport/Control", body, headers);
    }

    public static void radio(Speaker speaker, String url) throws Exception {
        if (!url.startsWith("http://")) {
            throw new IllegalArgumentException("La Bose nécessite une URL radio http://");
        }
        soap(speaker, "SetAVTransportURI", "<InstanceID>0</InstanceID><CurrentURI>"
                + esc(url) + "</CurrentURI><CurrentURIMetaData></CurrentURIMetaData>");
        soap(speaker, "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>");
    }

    private static String deviceId(Speaker speaker) throws Exception {
        String id = get(speaker.host, "info").getAttribute("deviceID");
        if (id.isEmpty()) throw new IOException("Identité absente : " + speaker.name);
        return id;
    }

    public static GroupState groupState() throws Exception {
        String verandaId = deviceId(VERANDA);
        String cuisineId = deviceId(CUISINE);
        Element verandaZone = get(VERANDA.host, "getZone");
        Element cuisineZone = get(CUISINE.host, "getZone");
        String verandaMaster = verandaZone.getAttribute("master");
        String cuisineMaster = cuisineZone.getAttribute("master");
        if (verandaMaster.isEmpty() && cuisineMaster.isEmpty()) {
            return new GroupState(false, false, null);
        }
        if (verandaMaster.equals(cuisineMaster)) {
            Speaker master = verandaMaster.equals(verandaId) ? VERANDA
                    : verandaMaster.equals(cuisineId) ? CUISINE : null;
            if (master != null) {
                Element masterZone = master == VERANDA ? verandaZone : cuisineZone;
                Set<String> members = new HashSet<>();
                NodeList nodes = masterZone.getElementsByTagName("member");
                for (int i = 0; i < nodes.getLength(); i++) {
                    members.add(((Element) nodes.item(i)).getAttribute("ipaddress"));
                }
                if (members.size() == 2 && members.contains(VERANDA.host)
                        && members.contains(CUISINE.host)) {
                    return new GroupState(true, false, master);
                }
            }
        }
        return new GroupState(false, true, null);
    }

    private static boolean isPlaying(Speaker speaker) throws Exception {
        Element playing = get(speaker.host, "now_playing");
        String state = text(playing, "playStatus");
        return !"STANDBY".equals(playing.getAttribute("source"))
                && ("PLAY_STATE".equals(state) || "PAUSE_STATE".equals(state));
    }

    private static String localAddressFor(String host) throws Exception {
        try (DatagramSocket socket = new DatagramSocket()) {
            socket.connect(InetAddress.getByName(host), 8090);
            return socket.getLocalAddress().getHostAddress();
        }
    }

    public static GroupState toggleGroup(Speaker selected) throws Exception {
        GroupState before = groupState();
        if (before.conflict) {
            throw new IOException("Une enceinte appartient à un autre groupe");
        }
        if (before.active) {
            Speaker slave = before.master == VERANDA ? CUISINE : VERANDA;
            String body = "<zone master=\"" + deviceId(before.master) + "\">"
                    + "<member ipaddress=\"" + slave.host + "\">" + deviceId(slave)
                    + "</member></zone>";
            post(before.master.host, "removeZoneSlave", body);
        } else {
            Speaker master = selected == CUISINE ? CUISINE : VERANDA;
            Speaker slave = master == VERANDA ? CUISINE : VERANDA;
            if (!isPlaying(master) && isPlaying(slave)) {
                Speaker swap = master;
                master = slave;
                slave = swap;
            }
            String body = "<zone master=\"" + deviceId(master) + "\" senderIPAddress=\""
                    + esc(localAddressFor(master.host)) + "\">"
                    + "<member ipaddress=\"" + master.host + "\">" + deviceId(master) + "</member>"
                    + "<member ipaddress=\"" + slave.host + "\">" + deviceId(slave) + "</member>"
                    + "</zone>";
            post(master.host, "setZone", body);
        }
        for (int attempt = 0; attempt < 10; attempt++) {
            Thread.sleep(500);
            GroupState after = groupState();
            if (!after.conflict && after.active != before.active) return after;
        }
        throw new IOException("État du groupe non confirmé ; actualiser avant de réessayer");
    }

    public static void reboot(Speaker speaker) throws Exception {
        try (Socket socket = new Socket()) {
            socket.connect(new java.net.InetSocketAddress(speaker.host, 17000), TIMEOUT_MS);
            socket.setSoTimeout(TIMEOUT_MS);
            InputStream input = socket.getInputStream();
            ByteArrayOutputStream banner = new ByteArrayOutputStream();
            while (banner.size() < 4096) {
                int next = input.read();
                if (next == -1) throw new IOException("Console fermée avant son invite");
                banner.write(next);
                if (banner.toString("UTF-8").trim().endsWith("->")) break;
            }
            if (!banner.toString("UTF-8").trim().endsWith("->")) {
                throw new IOException("Invite de diagnostic absente ; reboot non envoyé");
            }
            socket.getOutputStream().write("sys reboot\r\n".getBytes(StandardCharsets.US_ASCII));
            socket.getOutputStream().flush();
        }
    }
}
