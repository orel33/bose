package fr.orel.bose;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int BACKGROUND = Color.rgb(12, 23, 37);
    private static final int CARD = Color.rgb(24, 40, 58);
    private static final int CARD_LIGHT = Color.rgb(32, 52, 72);
    private static final int ACCENT = Color.rgb(83, 205, 187);
    private static final int TEXT = Color.rgb(242, 248, 250);
    private static final int MUTED = Color.rgb(160, 179, 193);
    private static final int WARNING = Color.rgb(255, 174, 119);

    private interface Action { void run() throws Exception; }

    private static final class Radio {
        final String name;
        final String url;
        Radio(String name, String url) { this.name = name; this.url = url; }
    }

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final TextView[] speakerTabs = new TextView[3];
    private final List<Radio> radios = new ArrayList<>();
    private BoseClient.Speaker selected = BoseClient.VERANDA;
    private TextView titleView;
    private TextView detailView;
    private TextView volumeView;
    private TextView messageView;
    private TextView groupStatusView;
    private TextView groupButton;
    private TextView muteButton;
    private SeekBar volumeBar;
    private boolean trackingVolume;
    private boolean visible;
    private int refreshGeneration;

    private final Runnable periodicRefresh = new Runnable() {
        @Override public void run() {
            if (!visible) return;
            refresh();
            handler.postDelayed(this, 20000);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(BACKGROUND);
        getWindow().setNavigationBarColor(BACKGROUND);
        if (state != null) {
            int index = state.getInt("speaker", 0);
            selected = BoseClient.SPEAKERS[Math.max(0, Math.min(2, index))];
        }
        try {
            loadRadios();
        } catch (Exception error) {
            // The build includes this file; a visible error is still preferable to a crash.
        }
        buildScreen();
    }

    @Override protected void onResume() {
        super.onResume();
        visible = true;
        handler.removeCallbacks(periodicRefresh);
        periodicRefresh.run();
    }

    @Override protected void onPause() {
        visible = false;
        handler.removeCallbacks(periodicRefresh);
        super.onPause();
    }

    @Override protected void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        for (int i = 0; i < BoseClient.SPEAKERS.length; i++) {
            if (selected == BoseClient.SPEAKERS[i]) state.putInt("speaker", i);
        }
        super.onSaveInstanceState(state);
    }

    private int dp(float size) {
        return (int) (size * getResources().getDisplayMetrics().density + 0.5f);
    }

    private GradientDrawable background(int color, int radius) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(color);
        shape.setCornerRadius(dp(radius));
        return shape;
    }

    private TextView label(String value, float size, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return view;
    }

    private TextView button(String value, int color) {
        TextView view = label(value, 15, TEXT, true);
        view.setGravity(Gravity.CENTER);
        view.setPadding(dp(10), dp(12), dp(10), dp(12));
        view.setMinHeight(dp(48));
        view.setBackground(background(color, 14));
        view.setClickable(true);
        view.setFocusable(true);
        return view;
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        return layout;
    }

    private LinearLayout row() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.HORIZONTAL);
        layout.setGravity(Gravity.CENTER_VERTICAL);
        return layout;
    }

    private LinearLayout.LayoutParams below(int top) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(top);
        return params;
    }

    private LinearLayout card() {
        LinearLayout panel = column();
        panel.setPadding(dp(18), dp(18), dp(18), dp(18));
        panel.setBackground(background(CARD, 20));
        return panel;
    }

    private void section(LinearLayout root, String text) {
        TextView heading = label(text, 12, ACCENT, true);
        heading.setLetterSpacing(0.12f);
        root.addView(heading, below(24));
    }

    private void buildScreen() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(BACKGROUND);
        LinearLayout root = column();
        root.setPadding(dp(18), dp(22), dp(18), dp(30));
        scroll.addView(root);
        setContentView(scroll);

        root.addView(label("BOSE MAISON", 12, ACCENT, true));
        root.addView(label("Vos trois enceintes", 29, TEXT, true), below(5));
        root.addView(label("Contrôle local sur le Wi-Fi de la maison", 14, MUTED, false), below(5));

        LinearLayout tabs = row();
        root.addView(tabs, below(22));
        for (int i = 0; i < BoseClient.SPEAKERS.length; i++) {
            final BoseClient.Speaker speaker = BoseClient.SPEAKERS[i];
            TextView tab = button(speaker.name, CARD_LIGHT);
            speakerTabs[i] = tab;
            LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, dp(50), 1);
            if (i > 0) p.leftMargin = dp(7);
            tabs.addView(tab, p);
            tab.setOnClickListener(view -> selectSpeaker(speaker));
        }

        LinearLayout statusCard = card();
        root.addView(statusCard, below(18));
        LinearLayout statusTop = row();
        statusCard.addView(statusTop);
        TextView statusHeading = label("EN CE MOMENT", 12, MUTED, true);
        statusTop.addView(statusHeading, new LinearLayout.LayoutParams(0, -2, 1));
        TextView refreshButton = label("Actualiser ↻", 13, ACCENT, true);
        refreshButton.setPadding(dp(5), dp(5), dp(5), dp(5));
        statusTop.addView(refreshButton);
        refreshButton.setOnClickListener(view -> refresh());
        titleView = label("Connexion…", 22, TEXT, true);
        statusCard.addView(titleView, below(16));
        detailView = label("", 14, MUTED, false);
        statusCard.addView(detailView, below(6));
        messageView = label("", 13, ACCENT, false);
        statusCard.addView(messageView, below(12));

        section(root, "RADIOS · LECTURE DIRECTE");
        if (radios.isEmpty()) {
            root.addView(label("Configuration des radios absente de l’APK", 14, WARNING, false), below(8));
        }
        for (int start = 0; start < radios.size(); start += 2) {
            LinearLayout radioRow = row();
            root.addView(radioRow, below(9));
            for (int index = start; index < Math.min(start + 2, radios.size()); index++) {
                Radio radio = radios.get(index);
                TextView radioButton = button((index + 1) + "  " + radio.name, CARD_LIGHT);
                LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, dp(55), 1);
                if (index > start) p.leftMargin = dp(9);
                radioRow.addView(radioButton, p);
                radioButton.setOnClickListener(view -> {
                    BoseClient.Speaker target = selected;
                    perform("Lecture de " + radio.name, target,
                            () -> BoseClient.radio(target, radio.url), true);
                });
            }
        }

        section(root, "LECTURE ET SON");
        LinearLayout controls = row();
        root.addView(controls, below(9));
        TextView playButton = button("Lecture / Pause", CARD_LIGHT);
        controls.addView(playButton, new LinearLayout.LayoutParams(0, dp(52), 1));
        playButton.setOnClickListener(view -> sendKey("PLAY_PAUSE", "Lecture / pause"));
        TextView stopButton = button("Arrêter", CARD_LIGHT);
        LinearLayout.LayoutParams stopParams = new LinearLayout.LayoutParams(0, dp(52), 1);
        stopParams.leftMargin = dp(9);
        controls.addView(stopButton, stopParams);
        stopButton.setOnClickListener(view -> sendKey("STOP", "Arrêt"));

        LinearLayout secondary = row();
        root.addView(secondary, below(9));
        muteButton = button("Couper le son", CARD_LIGHT);
        secondary.addView(muteButton, new LinearLayout.LayoutParams(0, dp(52), 1));
        muteButton.setOnClickListener(view -> sendKey("MUTE", "Son modifié"));
        TextView powerButton = button("Veille / Marche", CARD_LIGHT);
        LinearLayout.LayoutParams powerParams = new LinearLayout.LayoutParams(0, dp(52), 1);
        powerParams.leftMargin = dp(9);
        secondary.addView(powerButton, powerParams);
        powerButton.setOnClickListener(view -> sendKey("POWER", "Alimentation modifiée"));

        LinearLayout volumeCard = card();
        root.addView(volumeCard, below(13));
        LinearLayout volumeTop = row();
        volumeCard.addView(volumeTop);
        volumeTop.addView(label("Volume", 16, TEXT, true), new LinearLayout.LayoutParams(0, -2, 1));
        volumeView = label("—", 16, ACCENT, true);
        volumeTop.addView(volumeView);
        volumeBar = new SeekBar(this);
        volumeBar.setMax(100);
        volumeBar.setProgressTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        volumeBar.setThumbTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        volumeCard.addView(volumeBar, below(12));
        volumeBar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onStartTrackingTouch(SeekBar bar) { trackingVolume = true; }
            @Override public void onProgressChanged(SeekBar bar, int value, boolean fromUser) {
                if (fromUser) volumeView.setText(value + " %");
            }
            @Override public void onStopTrackingTouch(SeekBar bar) {
                trackingVolume = false;
                BoseClient.Speaker target = selected;
                int value = bar.getProgress();
                perform("Volume " + value + " %", target,
                        () -> BoseClient.setVolume(target, value), true);
            }
        });

        section(root, "GROUPE VERANDA + CUISINE");
        LinearLayout groupCard = card();
        root.addView(groupCard, below(9));
        groupStatusView = label("Lecture de l’état du groupe…", 14, MUTED, false);
        groupCard.addView(groupStatusView);
        groupButton = button("Former le groupe", ACCENT);
        groupButton.setTextColor(BACKGROUND);
        groupCard.addView(groupButton, below(12));
        groupButton.setOnClickListener(view -> {
            BoseClient.Speaker target = selected;
            perform("Bascule du groupe", target,
                    () -> BoseClient.toggleGroup(target), true);
        });

        section(root, "ENTRETIEN");
        TextView rebootButton = button("Redémarrer l’enceinte sélectionnée", Color.rgb(91, 52, 55));
        root.addView(rebootButton, below(9));
        rebootButton.setOnClickListener(view -> confirmReboot());
        root.addView(label("Le téléphone et les Bose doivent être sur le même réseau local.",
                12, MUTED, false), below(16));
        updateTabs();
    }

    private void loadRadios() throws Exception {
        Map<String, String> values = new HashMap<>();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                getAssets().open("radios.env"), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isEmpty() || line.startsWith("#") || !line.contains("=")) continue;
                int separator = line.indexOf('=');
                values.put(line.substring(0, separator), line.substring(separator + 1));
            }
        }
        for (int i = 1; i <= 5; i++) {
            String name = values.get("PRESET_" + i + "_NAME");
            String url = values.get("PRESET_" + i + "_URL");
            if (name == null || url == null) throw new IllegalStateException("Radio " + i + " absente");
            radios.add(new Radio(name, url));
        }
    }

    private void selectSpeaker(BoseClient.Speaker speaker) {
        selected = speaker;
        refreshGeneration++;
        updateTabs();
        titleView.setText("Connexion…");
        detailView.setText(speaker.host);
        messageView.setText("");
        refresh();
    }

    private void updateTabs() {
        for (int i = 0; i < speakerTabs.length; i++) {
            boolean current = selected == BoseClient.SPEAKERS[i];
            speakerTabs[i].setBackground(background(current ? ACCENT : CARD_LIGHT, 14));
            speakerTabs[i].setTextColor(current ? BACKGROUND : TEXT);
        }
    }

    private void refresh() {
        BoseClient.Speaker speaker = selected;
        int generation = ++refreshGeneration;
        worker.execute(() -> {
            BoseClient.Snapshot snapshot = null;
            BoseClient.GroupState group = null;
            String speakerError = null;
            String groupError = null;
            try { snapshot = BoseClient.snapshot(speaker); }
            catch (Exception error) { speakerError = error.getMessage(); }
            try { group = BoseClient.groupState(); }
            catch (Exception error) { groupError = error.getMessage(); }
            BoseClient.Snapshot finalSnapshot = snapshot;
            BoseClient.GroupState finalGroup = group;
            String finalSpeakerError = speakerError;
            String finalGroupError = groupError;
            runOnUiThread(() -> {
                if (selected != speaker || generation != refreshGeneration || isFinishing()) return;
                renderSnapshot(finalSnapshot, finalSpeakerError);
                renderGroup(finalGroup, finalGroupError);
            });
        });
    }

    private void renderSnapshot(BoseClient.Snapshot snapshot, String error) {
        if (snapshot == null) {
            titleView.setText("Enceinte inaccessible");
            detailView.setText(selected.name + " · " + selected.host);
            if (error != null) messageView.setText(error);
            return;
        }
        String title = snapshot.title;
        if ("STANDBY".equals(snapshot.source)) title = "En veille";
        else if (title.isEmpty()) title = "Source " + snapshot.source;
        titleView.setText(title);
        String state = "PLAY_STATE".equals(snapshot.playStatus) ? "En lecture"
                : "PAUSE_STATE".equals(snapshot.playStatus) ? "En pause"
                : "BUFFERING_STATE".equals(snapshot.playStatus) ? "Chargement"
                : "STANDBY".equals(snapshot.source) ? "En veille" : snapshot.playStatus;
        detailView.setText(snapshot.name + " · " + state);
        if (!trackingVolume) {
            volumeBar.setProgress(snapshot.volume);
            volumeView.setText(snapshot.volume + " %");
        }
        muteButton.setText(snapshot.muted ? "Rétablir le son" : "Couper le son");
    }

    private void renderGroup(BoseClient.GroupState group, String error) {
        if (group == null) {
            groupStatusView.setText("État indisponible" + (error == null ? "" : " · " + error));
            groupButton.setEnabled(false);
            groupButton.setAlpha(0.5f);
            return;
        }
        if (group.conflict) {
            groupStatusView.setText("Une enceinte appartient à un autre groupe");
            groupButton.setEnabled(false);
            groupButton.setAlpha(0.5f);
            return;
        }
        groupButton.setEnabled(true);
        groupButton.setAlpha(1f);
        groupStatusView.setText(group.active
                ? "Groupe actif · maître : " + group.master.name : "Groupe séparé");
        groupButton.setText(group.active ? "Séparer les enceintes" : "Former le groupe");
    }

    private void perform(String label, BoseClient.Speaker speaker, Action action, boolean refreshAfter) {
        messageView.setText(label + "…");
        worker.execute(() -> {
            try {
                action.run();
                runOnUiThread(() -> {
                    if (isFinishing()) return;
                    messageView.setText(label + " : envoyé");
                    if (refreshAfter) refresh();
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (isFinishing()) return;
                    messageView.setText("Échec sur " + speaker.name + " : " + error.getMessage());
                    if (refreshAfter) refresh();
                });
            }
        });
    }

    private void sendKey(String key, String label) {
        BoseClient.Speaker target = selected;
        perform(label, target, () -> BoseClient.key(target, key), true);
    }

    private void confirmReboot() {
        BoseClient.Speaker target = selected;
        new AlertDialog.Builder(this)
                .setTitle("Redémarrer " + target.name + " ?")
                .setMessage("L’enceinte sera indisponible pendant environ une minute.")
                .setNegativeButton("Annuler", null)
                .setPositiveButton("Redémarrer", (dialog, which) -> perform(
                        "Redémarrage de " + target.name, target,
                        () -> BoseClient.reboot(target), false))
                .show();
    }
}
