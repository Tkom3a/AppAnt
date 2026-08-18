import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

type Miner = {
  id: string;
  name: string;
  firmware: string;
  port: number;
  expected_ths: number;
  status: string;
  online: boolean;
  hashrate_ths: number | null;
  temperature_c: number | null;
  last_ping_at: string | null;
  last_error: string | null;
};

type Payload = {
  location_ip: string;
  auto_reboot_enabled: boolean;
  miners: Miner[];
};

type EventItem = {
  ts: string;
  message: string;
};

const STATUS: Record<string, string> = {
  healthy: "норма",
  low: "низкий хэшрейт",
  down: "нет хэшрейта",
  rebooting: "ребут",
  cooldown: "ожидание",
  unknown: "нет данных",
};

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

function normalizeUrl(raw: string) {
  let url = raw.trim().replace(/\/$/, "");
  if (!url) return url;
  if (!url.includes("://")) url = `http://${url}`;
  if (url.startsWith("https://")) {
    const host = url.slice("https://".length).split("/")[0].split(":")[0];
    if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) {
      url = `http://${url.slice("https://".length)}`;
    }
  }
  return url;
}

async function api(url: string, token: string, path: string, init: RequestInit = {}) {
  const response = await fetch(`${url}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Token": token,
      ...(init.headers || {}),
    },
  });
  if (response.status === 401) throw new Error("Неверный API токен");
  if (!response.ok) throw new Error(`Ошибка ядра: ${response.status}`);
  return response.json();
}

function fmtTime(value: string | null) {
  if (!value) return "ещё не было";
  return new Date(value).toLocaleString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
  });
}

export default function App() {
  const [url, setUrl] = useState("http://95.165.139.60:8486");
  const [token, setToken] = useState("");
  const [ready, setReady] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<Payload | null>(null);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      const savedUrl = (await AsyncStorage.getItem("coreUrl")) || "http://95.165.139.60:8486";
      const savedToken = (await AsyncStorage.getItem("apiToken")) || "";
      setUrl(savedUrl);
      setToken(savedToken);
      setConfigured(Boolean(savedUrl && savedToken));
      setReady(true);
    })();
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [miners, eventPayload] = await Promise.all([
        api(url, token, "/api/miners"),
        api(url, token, "/api/events"),
      ]);
      setData(miners);
      setEvents(eventPayload.events || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки");
    } finally {
      setLoading(false);
    }
  }, [url, token]);

  useEffect(() => {
    if (configured && url && token) {
      load();
      registerPush(url, token);
    }
  }, [configured, url, token, load]);

  const total = useMemo(
    () => (data?.miners || []).reduce((sum, miner) => sum + (miner.hashrate_ths || 0), 0),
    [data]
  );

  if (!ready) {
    return (
      <View style={styles.center}>
        <Image source={require("./assets/icon.png")} style={styles.bootLogo} />
        <ActivityIndicator color="#ffffff" style={{ marginTop: 16 }} />
      </View>
    );
  }

  if (!configured) {
    return (
      <SafeAreaView style={styles.safe}>
        <StatusBar style="light" />
        <View style={styles.pad}>
          <Image source={require("./assets/icon.png")} style={styles.logo} />
          <Text style={styles.eyebrow}>ЯДРО</Text>
          <Text style={styles.title}>УВЫ control</Text>
          <Text style={styles.hint}>
            Адрес ядра и API_TOKEN из файла .env на сервере. Только http, порт 8486.
          </Text>
          <Text style={styles.label}>URL ядра</Text>
          <TextInput
            style={styles.input}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="http://95.165.139.60:8486"
            placeholderTextColor="#6d7680"
            value={url}
            onChangeText={setUrl}
          />
          <Text style={styles.label}>API токен</Text>
          <TextInput
            style={styles.input}
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
            placeholder="API_TOKEN"
            placeholderTextColor="#6d7680"
            value={token}
            onChangeText={setToken}
          />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Pressable
            style={styles.primary}
            onPress={async () => {
              const clean = normalizeUrl(url);
              const key = token.trim();
              try {
                await api(clean, key, "/api/miners");
                await AsyncStorage.setItem("coreUrl", clean);
                await AsyncStorage.setItem("apiToken", key);
                setUrl(clean);
                setConfigured(true);
              } catch (err) {
                setError(err instanceof Error ? err.message : "Не удалось войти");
              }
            }}
          >
            <Text style={styles.primaryText}>Подключить</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar style="light" />
      <ScrollView
        contentContainerStyle={styles.pad}
        refreshControl={
          <RefreshControl refreshing={loading} onRefresh={load} tintColor="#ffffff" />
        }
      >
        <View style={styles.header}>
          <Image source={require("./assets/icon.png")} style={styles.headerLogo} />
          <View style={{ flex: 1 }}>
            <Text style={styles.eyebrow}>ЛОКАЦИЯ</Text>
            <Text style={styles.title}>{data?.location_ip || "УВЫ control"}</Text>
          </View>
        </View>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Pressable
          style={styles.ghost}
          onPress={async () => {
            try {
              await api(url, token, "/api/miners/refresh", { method: "POST" });
              await load();
            } catch (err) {
              setError(err instanceof Error ? err.message : "Ошибка обновления");
            }
          }}
        >
          <Text style={styles.ghostText}>Обновить сейчас</Text>
        </Pressable>
        <View style={styles.stats}>
          <View style={styles.stat}>
            <Text style={styles.meta}>Всего TH/s</Text>
            <Text style={styles.statValue}>{total.toFixed(1)}</Text>
          </View>
          <View style={styles.stat}>
            <Text style={styles.meta}>Авторебут</Text>
            <Text style={styles.statValue}>{data?.auto_reboot_enabled ? "вкл" : "выкл"}</Text>
          </View>
        </View>
        <View style={styles.switchRow}>
          <Text style={styles.switchLabel}>Автоперезагрузка при падении</Text>
          <Switch
            value={Boolean(data?.auto_reboot_enabled)}
            onValueChange={async (value) => {
              await api(url, token, "/api/settings", {
                method: "PATCH",
                body: JSON.stringify({ auto_reboot_enabled: value }),
              });
              load();
            }}
            trackColor={{ true: "#ffffff", false: "#333" }}
          />
        </View>
        {(data?.miners || []).map((miner) => (
          <View key={miner.id} style={styles.card}>
            <View style={styles.row}>
              <Text style={styles.name}>{miner.name}</Text>
              <Text style={[styles.badge, miner.status === "healthy" && styles.badgeOk, miner.status === "down" && styles.badgeBad]}>
                {STATUS[miner.status] || miner.status}
              </Text>
            </View>
            <Text style={styles.hash}>
              {miner.hashrate_ths == null ? "—" : `${miner.hashrate_ths.toFixed(1)} TH/s`}
            </Text>
            <Text style={styles.meta}>
              {miner.firmware} · порт {miner.port}
              {miner.temperature_c != null ? ` · ${Math.round(miner.temperature_c)}°C` : ""}
              {"\n"}последний пинг: {fmtTime(miner.last_ping_at)}
              {miner.last_error ? `\nошибка: ${miner.last_error}` : ""}
            </Text>
            <Pressable
              style={styles.reboot}
              onPress={() =>
                Alert.alert("Перезагрузка", `Ребутнуть ${miner.name}?`, [
                  { text: "Отмена", style: "cancel" },
                  {
                    text: "Перезагрузить",
                    style: "destructive",
                    onPress: async () => {
                      await api(url, token, `/api/miners/${miner.id}/reboot`, {
                        method: "POST",
                      });
                      load();
                    },
                  },
                ])
              }
            >
              <Text style={styles.rebootText}>Перезагрузить</Text>
            </Pressable>
          </View>
        ))}
        <Text style={styles.section}>События</Text>
        {events.slice(0, 12).map((event, index) => (
          <View key={`${event.ts}-${index}`} style={styles.event}>
            <Text style={styles.meta}>{fmtTime(event.ts)}</Text>
            <Text style={styles.eventText}>{event.message}</Text>
          </View>
        ))}
        <Pressable
          style={[styles.ghost, { marginTop: 8 }]}
          onPress={async () => {
            await AsyncStorage.multiRemove(["coreUrl", "apiToken"]);
            setConfigured(false);
            setData(null);
          }}
        >
          <Text style={styles.ghostText}>Сменить сервер</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

async function registerPush(url: string, token: string) {
  try {
    if (!Device.isDevice) return;
    const permission = await Notifications.requestPermissionsAsync();
    if (permission.status !== "granted") return;
    const push = await Notifications.getExpoPushTokenAsync();
    await api(url, token, "/api/devices", {
      method: "POST",
      body: JSON.stringify({ token: push.data, platform: "ios" }),
    });
  } catch {
    return;
  }
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#000000" },
  center: { flex: 1, backgroundColor: "#000000", alignItems: "center", justifyContent: "center" },
  pad: { padding: 16, paddingBottom: 40 },
  logo: { width: 96, height: 96, alignSelf: "center", marginBottom: 18, borderRadius: 16 },
  bootLogo: { width: 88, height: 88, borderRadius: 16 },
  header: { flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 8 },
  headerLogo: { width: 44, height: 44, borderRadius: 8 },
  eyebrow: { color: "#9aa3ad", letterSpacing: 1.5, fontSize: 11 },
  title: { color: "#ffffff", fontSize: 28, fontWeight: "700", marginTop: 4, marginBottom: 12 },
  hint: { color: "#9aa3ad", marginBottom: 16, lineHeight: 20 },
  label: { color: "#9aa3ad", marginBottom: 6, marginTop: 8 },
  input: {
    backgroundColor: "#15181d",
    borderColor: "#232830",
    borderWidth: 1,
    borderRadius: 12,
    color: "#ffffff",
    padding: 12,
  },
  primary: { backgroundColor: "#ffffff", borderRadius: 12, padding: 14, marginTop: 18 },
  primaryText: { textAlign: "center", fontWeight: "700", color: "#000000" },
  error: { color: "#ef5d5d", marginTop: 10 },
  stats: { flexDirection: "row", gap: 10, marginBottom: 12, marginTop: 12 },
  stat: {
    flex: 1,
    backgroundColor: "#15181d",
    borderColor: "#232830",
    borderWidth: 1,
    borderRadius: 16,
    padding: 14,
  },
  statValue: { color: "#ffffff", fontSize: 22, fontWeight: "700", marginTop: 6 },
  switchRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 14,
  },
  switchLabel: { color: "#ffffff", fontSize: 15, flex: 1, paddingRight: 12 },
  card: {
    backgroundColor: "#15181d",
    borderColor: "#232830",
    borderWidth: 1,
    borderRadius: 16,
    padding: 14,
    marginBottom: 10,
  },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  name: { color: "#ffffff", fontSize: 17, fontWeight: "700" },
  badge: { color: "#e8a54b", fontSize: 11, textTransform: "uppercase" },
  badgeOk: { color: "#3dcc8a" },
  badgeBad: { color: "#ef5d5d" },
  hash: { color: "#ffffff", fontSize: 26, marginVertical: 8 },
  meta: { color: "#9aa3ad", fontSize: 12, lineHeight: 18 },
  reboot: { marginTop: 12, backgroundColor: "#1a1a1a", borderRadius: 12, padding: 12, borderWidth: 1, borderColor: "#333" },
  rebootText: { color: "#ffffff", textAlign: "center", fontWeight: "700" },
  section: { color: "#ffffff", fontSize: 18, fontWeight: "700", marginTop: 12, marginBottom: 8 },
  event: {
    backgroundColor: "#15181d",
    borderColor: "#232830",
    borderWidth: 1,
    borderRadius: 12,
    padding: 10,
    marginBottom: 8,
  },
  eventText: { color: "#d7dbe0", marginTop: 4 },
  ghost: {
    marginTop: 4,
    borderColor: "#232830",
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
  },
  ghostText: { color: "#ffffff", textAlign: "center" },
});
