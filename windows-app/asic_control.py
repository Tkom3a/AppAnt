from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable, Optional

import customtkinter as ctk
import httpx
from PIL import Image, ImageTk

APP_NAME = "УВЫ control"

STATUS = {
    "healthy": "норма",
    "low": "низкий хэшрейт",
    "down": "нет хэшрейта",
    "rebooting": "ребут",
    "cooldown": "ожидание",
    "unknown": "нет данных",
}

STATUS_COLOR = {
    "healthy": "#3dcc8a",
    "low": "#e8a54b",
    "down": "#ef5d5d",
    "rebooting": "#e8a54b",
    "cooldown": "#e8a54b",
    "unknown": "#9aa3ad",
}

BG = "#0b0d10"
CARD = "#15181d"
LINE = "#232830"
TEXT = "#f4f1ea"
MUTED = "#9aa3ad"
ACCENT = "#e8a54b"


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return Path(base) / name


def logo_image(size: int = 64) -> Image.Image:
    path = resource_path("uvy-logo.png")
    return Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def appdata_dir() -> Path:
    root = Path.home() / "AppData" / "Roaming" / "ASICControl"
    root.mkdir(parents=True, exist_ok=True)
    return root


def settings_path() -> Path:
    return appdata_dir() / "settings.json"


def load_settings() -> dict[str, str]:
    path = settings_path()
    if not path.exists():
        return {"url": "", "token": "", "last_event_ts": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"url": "", "token": "", "last_event_ts": ""}
    return {
        "url": str(data.get("url", "")),
        "token": str(data.get("token", "")),
        "last_event_ts": str(data.get("last_event_ts", "")),
    }


def save_settings(data: dict[str, str]) -> None:
    settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def fmt_time(value: Optional[str]) -> str:
    if not value:
        return "ещё не было"
    try:
        return datetime.fromisoformat(value).strftime("%d.%m %H:%M")
    except ValueError:
        return value


def _host_is_ip(host: str) -> bool:
    host = host.split("%")[0]
    if host.startswith("[") and host.endswith("]"):
        return True
    parts = host.split(".")
    return len(parts) == 4 and all(part.isdigit() for part in parts)


def normalize_core_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        return url
    if "://" not in url:
        url = "http://" + url
    if url.startswith("https://"):
        hostport = url[len("https://") :].split("/", 1)[0]
        host = hostport.split(":")[0]
        # Ядро без nginx — обычный HTTP. Для IP https почти всегда ошибка.
        if _host_is_ip(host):
            url = "http://" + url[len("https://") :]
    return url


def friendly_http_error(exc: Exception, url: str) -> str:
    text = str(exc)
    lowered = text.lower()
    if "10061" in text or "connection refused" in lowered or "actively refused" in lowered:
        return (
            "Порт закрыт: компьютер на том конце отверг подключение.\n"
            "Обычно так бывает, если:\n"
            "• в URL стоит https:// — нужно http://\n"
            "• ядро не запущено (docker compose ps)\n"
            "• проброшен не тот порт (ядро слушает 8486)\n"
            f"Сейчас пробуем: {url}"
        )
    if "10060" in text or "timed out" in lowered or "timeout" in lowered:
        return (
            "Нет ответа от сервера (таймаут).\n"
            "Проверьте белый IP, файрвол и что вы не сидите в той же сети, "
            "что и роутер — с локации иногда не открывается свой же белый IP. "
            "Тогда впишите локальный адрес ядра, например http://192.168.1.10:8486"
        )
    if "ssl" in lowered or "certificate" in lowered or "wrong_version_number" in lowered:
        return (
            "Это не HTTPS-сервер. Укажите http:// а не https://\n"
            f"Пример: {url.replace('https://', 'http://', 1)}"
        )
    if "401" in text or "токен" in lowered:
        return "Неверный API токен — возьмите API_TOKEN из .env на сервере."
    return text


def toast(title: str, message: str) -> None:
    try:
        from winotify import Notification

        note = Notification(app_id=APP_NAME, title=title, msg=message)
        try:
            note.set_audio("ms-winsoundevent:Notification.Default", loop=False)
        except Exception:
            pass
        note.show()
    except Exception:
        return


class Api:
    def __init__(self) -> None:
        self.url = ""
        self.token = ""

    def configure(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token.strip()

    def _request(self, method: str, path: str, json_body: Any = None) -> Any:
        try:
            with httpx.Client(timeout=20.0, verify=False) as client:
                response = client.request(
                    method,
                    f"{self.url}{path}",
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Token": self.token,
                    },
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(friendly_http_error(exc, self.url)) from exc
        if response.status_code == 401:
            raise RuntimeError("Неверный API токен")
        if response.status_code >= 400:
            raise RuntimeError(f"Ошибка ядра: {response.status_code}")
        return response.json()

    def miners(self) -> dict:
        return self._request("GET", "/api/miners")

    def events(self) -> dict:
        return self._request("GET", "/api/events")

    def refresh(self) -> dict:
        return self._request("POST", "/api/miners/refresh")

    def reboot(self, miner_id: str) -> dict:
        return self._request("POST", f"/api/miners/{miner_id}/reboot")

    def set_auto_reboot(self, enabled: bool) -> dict:
        return self._request(
            "PATCH", "/api/settings", {"auto_reboot_enabled": enabled}
        )


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1100x720")
        self.minsize(860, 560)
        self.configure(fg_color=BG)
        self._set_window_icon()
        self.api = Api()
        self.settings = load_settings()
        self.poll_job: Optional[str] = None
        self.busy = False
        self.tray_icon = None
        self._ignore_switch = False

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.login_frame = ctk.CTkFrame(self, fg_color=BG)
        self.dash_frame = ctk.CTkFrame(self, fg_color=BG)
        self._build_login()
        self._build_dashboard()

        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.after(200, self._start_tray)

        if self.settings.get("url") and self.settings.get("token"):
            self.url_entry.insert(0, self.settings["url"])
            self.token_entry.insert(0, self.settings["token"])
            self._connect()
        else:
            self._show_login()

    def _set_window_icon(self) -> None:
        ico = resource_path("uvy-logo.ico")
        png = resource_path("uvy-logo.png")
        try:
            if ico.exists():
                self.iconbitmap(str(ico))
            if png.exists():
                self._wm_icon = ImageTk.PhotoImage(Image.open(png).resize((32, 32)))
                self.iconphoto(True, self._wm_icon)
        except Exception:
            return

    def _build_login(self) -> None:
        box = ctk.CTkFrame(self.login_frame, fg_color=CARD, corner_radius=16)
        box.place(relx=0.5, rely=0.5, anchor="center")
        try:
            self._logo_ctk = ctk.CTkImage(
                light_image=logo_image(72),
                dark_image=logo_image(72),
                size=(72, 72),
            )
            ctk.CTkLabel(box, image=self._logo_ctk, text="").pack(padx=36, pady=(24, 6))
        except Exception:
            pass
        ctk.CTkLabel(
            box, text="УВЫ CONTROL", text_color=MUTED, font=("", 12)
        ).pack(padx=36, pady=(0, 4))
        ctk.CTkLabel(
            box,
            text="Подключение к ядру",
            text_color=TEXT,
            font=("", 22, "bold"),
        ).pack(padx=36, pady=(0, 8))
        ctk.CTkLabel(
            box,
            text="Тот же URL и API_TOKEN, что в приложении на iPhone.",
            text_color=MUTED,
            wraplength=420,
        ).pack(padx=36, pady=(0, 16))
        ctk.CTkLabel(box, text="URL ядра", text_color=MUTED, anchor="w").pack(
            fill="x", padx=36
        )
        self.url_entry = ctk.CTkEntry(
            box,
            width=420,
            height=40,
            placeholder_text="http://95.165.139.60:8486",
            fg_color=BG,
            border_color=LINE,
        )
        self.url_entry.pack(padx=36, pady=(4, 12))
        ctk.CTkLabel(box, text="API токен", text_color=MUTED, anchor="w").pack(
            fill="x", padx=36
        )
        self.token_entry = ctk.CTkEntry(
            box,
            width=420,
            height=40,
            show="•",
            placeholder_text="API_TOKEN из .env",
            fg_color=BG,
            border_color=LINE,
        )
        self.token_entry.pack(padx=36, pady=(4, 8))
        self.login_error = ctk.CTkLabel(
            box,
            text="",
            text_color="#ef5d5d",
            wraplength=420,
            justify="left",
        )
        self.login_error.pack(padx=36, pady=(0, 4))
        ctk.CTkButton(
            box,
            text="Подключить",
            fg_color=ACCENT,
            hover_color="#c48a3d",
            text_color="#1a1206",
            height=42,
            command=self._connect,
        ).pack(padx=36, pady=(8, 28), fill="x")

    def _build_dashboard(self) -> None:
        header = ctk.CTkFrame(self.dash_frame, fg_color=BG)
        header.pack(fill="x", padx=20, pady=(16, 8))
        left = ctk.CTkFrame(header, fg_color=BG)
        left.pack(side="left")
        ctk.CTkLabel(left, text="ЛОКАЦИЯ", text_color=MUTED, font=("", 11)).pack(
            anchor="w"
        )
        self.title_label = ctk.CTkLabel(
            left, text=APP_NAME, text_color=TEXT, font=("", 26, "bold")
        )
        self.title_label.pack(anchor="w")
        btns = ctk.CTkFrame(header, fg_color=BG)
        btns.pack(side="right")
        ctk.CTkButton(
            btns,
            text="Обновить сейчас",
            fg_color=CARD,
            hover_color=LINE,
            border_color=LINE,
            border_width=1,
            text_color=TEXT,
            command=self._force_refresh,
            width=160,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btns,
            text="Сменить сервер",
            fg_color=CARD,
            hover_color=LINE,
            border_color=LINE,
            border_width=1,
            text_color=TEXT,
            command=self._disconnect,
            width=140,
        ).pack(side="left")

        stats = ctk.CTkFrame(self.dash_frame, fg_color=BG)
        stats.pack(fill="x", padx=20, pady=8)
        self.total_card = self._stat_card(stats, "Всего TH/s", "—")
        self.auto_card = self._stat_card(stats, "Авторебут", "—")
        self.total_card.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.auto_card.pack(side="left", fill="x", expand=True, padx=(8, 0))

        switch_row = ctk.CTkFrame(self.dash_frame, fg_color=BG)
        switch_row.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkLabel(
            switch_row,
            text="Автоперезагрузка при падении хэшрейта",
            text_color=TEXT,
        ).pack(side="left")
        self.auto_switch = ctk.CTkSwitch(
            switch_row,
            text="",
            command=self._toggle_auto,
            progress_color=ACCENT,
        )
        self.auto_switch.pack(side="right")

        self.status_line = ctk.CTkLabel(
            self.dash_frame, text="", text_color=MUTED, anchor="w"
        )
        self.status_line.pack(fill="x", padx=20)

        body = ctk.CTkFrame(self.dash_frame, fg_color=BG)
        body.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        miners_col = ctk.CTkFrame(body, fg_color=BG)
        miners_col.pack(side="left", fill="both", expand=True, padx=(0, 8))
        ctk.CTkLabel(
            miners_col, text="Асики", text_color=TEXT, font=("", 16, "bold")
        ).pack(anchor="w", pady=(0, 6))
        self.miners_list = ctk.CTkScrollableFrame(
            miners_col, fg_color=BG, scrollbar_button_color=LINE
        )
        self.miners_list.pack(fill="both", expand=True)

        events_col = ctk.CTkFrame(body, fg_color=BG)
        events_col.pack(side="right", fill="both", expand=True, padx=(8, 0))
        ctk.CTkLabel(
            events_col, text="События", text_color=TEXT, font=("", 16, "bold")
        ).pack(anchor="w", pady=(0, 6))
        self.events_list = ctk.CTkScrollableFrame(
            events_col, fg_color=BG, scrollbar_button_color=LINE
        )
        self.events_list.pack(fill="both", expand=True)

    def _stat_card(self, parent: ctk.CTkFrame, caption: str, value: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, border_color=LINE, border_width=1)
        ctk.CTkLabel(card, text=caption, text_color=MUTED).pack(anchor="w", padx=16, pady=(12, 0))
        label = ctk.CTkLabel(card, text=value, text_color=TEXT, font=("", 22, "bold"))
        label.pack(anchor="w", padx=16, pady=(4, 14))
        card.value_label = label  # type: ignore[attr-defined]
        return card

    def _show_login(self, error: str = "") -> None:
        self._stop_poll()
        self.dash_frame.pack_forget()
        self.login_frame.pack(fill="both", expand=True)
        self.login_error.configure(text=error)

    def _show_dash(self) -> None:
        self.login_frame.pack_forget()
        self.dash_frame.pack(fill="both", expand=True)

    def _connect(self) -> None:
        url = normalize_core_url(self.url_entry.get())
        token = self.token_entry.get().strip()
        if not url or not token:
            self.login_error.configure(text="Введите URL и токен")
            return
        if url != self.url_entry.get().strip().rstrip("/"):
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, url)
        self.api.configure(url, token)
        self.login_error.configure(text="Подключаюсь…")
        self._run_async(
            lambda: (self.api.miners(), self.api.events()),
            lambda result, error: self._on_connected(url, token, result, error),
        )

    def _on_connected(self, url: str, token: str, result: Any, error: Optional[Exception]) -> None:
        if error:
            self._show_login(friendly_http_error(error, url))
            return
        miners, events = result
        self.settings["url"] = url
        self.settings["token"] = token
        save_settings(self.settings)
        self._show_dash()
        self._render(miners, events, notify=False)
        self._notify_new_events(events)
        self._start_poll()

    def _disconnect(self) -> None:
        self.settings["url"] = ""
        self.settings["token"] = ""
        save_settings(self.settings)
        self._show_login()

    def _start_poll(self) -> None:
        self._stop_poll()
        self._poll()

    def _stop_poll(self) -> None:
        if self.poll_job:
            self.after_cancel(self.poll_job)
            self.poll_job = None

    def _poll(self) -> None:
        if not self.busy:
            self._load(notify=True)
        self.poll_job = self.after(30000, self._poll)

    def _force_refresh(self) -> None:
        self.status_line.configure(text="Запрашиваю хэшрейт у асиков…")

        def work():
            self.api.refresh()
            return self.api.miners(), self.api.events()

        self._run_async(
            work,
            lambda result, error: self._on_loaded(result, error, notify=True),
        )

    def _load(self, notify: bool) -> None:
        self._run_async(
            lambda: (self.api.miners(), self.api.events()),
            lambda result, error: self._on_loaded(result, error, notify=notify),
        )

    def _on_loaded(self, result: Any, error: Optional[Exception], notify: bool) -> None:
        if error:
            self.status_line.configure(text=str(error))
            return
        miners, events = result
        self._render(miners, events, notify=notify)

    def _render(self, miners_payload: dict, events_payload: dict, notify: bool) -> None:
        miners = miners_payload.get("miners") or []
        events = events_payload.get("events") or []
        total = sum(item.get("hashrate_ths") or 0 for item in miners)
        auto = bool(miners_payload.get("auto_reboot_enabled"))
        self.title_label.configure(text=miners_payload.get("location_ip") or APP_NAME)
        self.total_card.value_label.configure(text=f"{total:.1f}")
        self.auto_card.value_label.configure(text="вкл" if auto else "выкл")
        self._ignore_switch = True
        if auto:
            self.auto_switch.select()
        else:
            self.auto_switch.deselect()
        self._ignore_switch = False
        self.status_line.configure(
            text=f"Последнее обновление: {datetime.now().strftime('%H:%M:%S')}"
        )
        self._render_miners(miners)
        self._render_events(events)
        if notify:
            self._notify_new_events(events)

    def _render_miners(self, miners: list[dict]) -> None:
        for child in self.miners_list.winfo_children():
            child.destroy()
        if not miners:
            ctk.CTkLabel(
                self.miners_list, text="Асики не найдены", text_color=MUTED
            ).pack(anchor="w")
            return
        for miner in miners:
            card = ctk.CTkFrame(
                self.miners_list,
                fg_color=CARD,
                corner_radius=14,
                border_color=LINE,
                border_width=1,
            )
            card.pack(fill="x", pady=6)
            top = ctk.CTkFrame(card, fg_color=CARD)
            top.pack(fill="x", padx=14, pady=(12, 0))
            ctk.CTkLabel(
                top, text=miner.get("name", miner.get("id")), text_color=TEXT, font=("", 16, "bold")
            ).pack(side="left")
            status = miner.get("status") or "unknown"
            ctk.CTkLabel(
                top,
                text=STATUS.get(status, status),
                text_color=STATUS_COLOR.get(status, MUTED),
                font=("", 12),
            ).pack(side="right")
            hashrate = miner.get("hashrate_ths")
            hs_text = "—" if hashrate is None else f"{hashrate:.1f} TH/s"
            ctk.CTkLabel(card, text=hs_text, text_color=TEXT, font=("", 24, "bold")).pack(
                anchor="w", padx=14, pady=(6, 0)
            )
            temp = miner.get("temperature_c")
            temp_text = f" · {round(temp)}°C" if temp is not None else ""
            error = miner.get("last_error")
            meta = (
                f"{miner.get('firmware')} · порт {miner.get('port')}{temp_text}\n"
                f"последний пинг: {fmt_time(miner.get('last_ping_at'))}"
            )
            if error:
                meta += f"\nошибка: {error}"
            ctk.CTkLabel(card, text=meta, text_color=MUTED, justify="left", anchor="w").pack(
                fill="x", padx=14, pady=(4, 0)
            )
            miner_id = miner.get("id")
            ctk.CTkButton(
                card,
                text="Перезагрузить",
                fg_color="#2a1d12",
                hover_color="#3a2818",
                text_color=ACCENT,
                command=lambda mid=miner_id, name=miner.get("name"): self._reboot(mid, name),
            ).pack(fill="x", padx=14, pady=(10, 14))

    def _render_events(self, events: list[dict]) -> None:
        for child in self.events_list.winfo_children():
            child.destroy()
        if not events:
            ctk.CTkLabel(
                self.events_list, text="Событий пока нет", text_color=MUTED
            ).pack(anchor="w")
            return
        for event in events[:30]:
            card = ctk.CTkFrame(
                self.events_list,
                fg_color=CARD,
                corner_radius=12,
                border_color=LINE,
                border_width=1,
            )
            card.pack(fill="x", pady=4)
            ctk.CTkLabel(
                card, text=fmt_time(event.get("ts")), text_color=MUTED, font=("", 11)
            ).pack(anchor="w", padx=12, pady=(8, 0))
            ctk.CTkLabel(
                card,
                text=event.get("message") or "",
                text_color=TEXT,
                wraplength=420,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=12, pady=(2, 10))

    def _notify_new_events(self, events: list[dict]) -> None:
        last = self.settings.get("last_event_ts") or ""
        fresh = []
        for event in events:
            ts = event.get("ts") or ""
            if last and ts <= last:
                continue
            if event.get("level") in {"warn", "crit"}:
                fresh.append(event)
        if events:
            self.settings["last_event_ts"] = events[0].get("ts") or last
            save_settings(self.settings)
        if not last:
            return
        for event in reversed(fresh[:3]):
            toast(APP_NAME, event.get("message") or "Событие фермы")

    def _toggle_auto(self) -> None:
        if self._ignore_switch:
            return
        enabled = bool(self.auto_switch.get())
        self._run_async(
            lambda: self.api.set_auto_reboot(enabled),
            lambda _result, error: self._after_toggle(error),
        )

    def _after_toggle(self, error: Optional[Exception]) -> None:
        if error:
            self.status_line.configure(text=str(error))
            return
        self._load(notify=False)

    def _reboot(self, miner_id: str, name: str) -> None:
        if not miner_id:
            return
        if not messagebox.askyesno("Перезагрузка", f"Ребутнуть {name}?"):
            return
        self.status_line.configure(text=f"Отправляю ребут: {name}")
        self._run_async(
            lambda: self.api.reboot(miner_id),
            lambda _result, error: self._after_reboot(name, error),
        )

    def _after_reboot(self, name: str, error: Optional[Exception]) -> None:
        if error:
            messagebox.showerror("Ошибка", str(error))
            return
        toast(APP_NAME, f"{name}: отправлена ручная перезагрузка")
        self._load(notify=False)

    def _run_async(self, work: Callable[[], Any], done: Callable[[Any, Optional[Exception]], None]) -> None:
        if self.busy:
            try:
                self.status_line.configure(text="Подождите, запрос ещё выполняется…")
            except Exception:
                pass
            return
        self.busy = True

        def worker() -> None:
            result = None
            error: Optional[Exception] = None
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                error = exc
            self.after(0, lambda: self._finish_async(done, result, error))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_async(
        self,
        done: Callable[[Any, Optional[Exception]], None],
        result: Any,
        error: Optional[Exception],
    ) -> None:
        self.busy = False
        done(result, error)

    def _hide_to_tray(self) -> None:
        self.withdraw()

    def _show_from_tray(self, _icon=None, _item=None) -> None:
        self.after(0, self._restore_window)

    def _restore_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_app(self, _icon=None, _item=None) -> None:
        self.after(0, self._destroy)

    def _destroy(self) -> None:
        self._stop_poll()
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()

    def _start_tray(self) -> None:
        try:
            import pystray
        except Exception:
            self.protocol("WM_DELETE_WINDOW", self._destroy)
            return

        try:
            image = logo_image(64)
        except Exception:
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Открыть", self._show_from_tray, default=True),
            pystray.MenuItem("Выход", self._quit_app),
        )
        self.tray_icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Это приложение рассчитано на Windows.")
    main()
