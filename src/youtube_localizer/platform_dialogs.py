"""Small platform account and episode pickers for the desktop application."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from .download.accounts import PLATFORMS, account_status, browser_sign_in, clear_account
from .download.platforms import list_platform_entries


class PlatformAccountsDialog:
    def __init__(self, parent: tk.Misc) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title("平台账号")
        self.window.geometry("660x290")
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body, text="在官方网站扫码登录；会话只保存在本机，不记录账号密码。", wraplength=600
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 18))
        self.states: dict[str, tk.StringVar] = {}
        self.finish = threading.Event()
        self.cancel = threading.Event()
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.active: str | None = None
        self.login_buttons = []
        for row, (platform, values) in enumerate(PLATFORMS.items(), 1):
            ttk.Label(body, text=values[0]).grid(
                row=row, column=0, sticky="w", padx=(0, 15), pady=10
            )
            state = self.states[platform] = tk.StringVar(value=account_status(platform))
            ttk.Label(body, textvariable=state).grid(row=row, column=1, sticky="w", padx=(0, 15))
            button = ttk.Button(
                body, text="登录 / 刷新", command=lambda key=platform: self._login(key)
            )
            button.grid(row=row, column=2, padx=5)
            self.login_buttons.append(button)
            ttk.Button(body, text="清除会话", command=lambda key=platform: self._clear(key)).grid(
                row=row, column=3
            )
        self.finish_button = ttk.Button(
            body, text="我已在网页完成登录，保存会话", state="disabled", command=self.finish.set
        )
        self.finish_button.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(20, 0))
        self.window.after(150, self._poll)

    def _login(self, platform: str) -> None:
        if self.active:
            return
        self.active = platform
        self.finish.clear()
        self.cancel.clear()
        for button in self.login_buttons:
            button.configure(state="disabled")
        self.finish_button.configure(state="normal")
        self.states[platform].set("请在新开的 Edge 窗口登录")

        def worker() -> None:
            try:
                browser_sign_in(platform, self.finish, self.cancel)
            except Exception:
                self.events.put((platform, "未能保存会话，请重新打开登录并完成扫码后再保存。"))
            else:
                self.events.put((platform, ""))

        # Give the browser's finally block time to close its isolated process on app exit.
        threading.Thread(target=worker, daemon=False).start()

    def _clear(self, platform: str) -> None:
        if self.active:
            return
        clear_account(platform)
        self.states[platform].set(account_status(platform))

    def _poll(self) -> None:
        if not self.window.winfo_exists():
            return
        try:
            platform, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.active = None
            self.states[platform].set(account_status(platform))
            self.finish_button.configure(state="disabled")
            for button in self.login_buttons:
                button.configure(state="normal")
            if error:
                messagebox.showerror("平台登录", error, parent=self.window)
        self.window.after(150, self._poll)

    def _close(self) -> None:
        self.cancel.set()
        self.window.destroy()


class PlatformVideosDialog:
    def __init__(self, parent: tk.Misc, initial: str, on_add: Callable[[list[str]], None]) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title("平台视频 / B站分P选择")
        self.window.geometry("760x480")
        self.window.transient(parent)
        self.on_add = on_add
        self.entries: list[tuple[str, str]] = []
        self.events: queue.Queue[object] = queue.Queue()
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body, text="粘贴 B站或抖音链接，也可粘贴完整分享文案。分P列表支持多选。", wraplength=710
        ).pack(anchor="w")
        self.value = tk.StringVar(value=initial)
        ttk.Entry(body, textvariable=self.value).pack(fill="x", pady=10)
        self.analyze = ttk.Button(body, text="读取视频 / 分P列表", command=self._analyze)
        self.analyze.pack(anchor="w")
        self.status = tk.StringVar(value="需要登录时，请先在主界面打开“平台账号”。")
        ttk.Label(body, textvariable=self.status, wraplength=710).pack(anchor="w", pady=10)
        self.list = tk.Listbox(body, selectmode="extended", exportselection=False, height=10)
        self.list.pack(fill="both", expand=True)
        ttk.Button(body, text="添加所选到任务队列", command=self._add).pack(
            anchor="e", pady=(15, 0)
        )
        self.window.after(150, self._poll)

    def _analyze(self) -> None:
        source = self.value.get()
        self.analyze.configure(state="disabled")
        self.status.set("正在读取平台信息…")

        def worker() -> None:
            try:
                self.events.put(list_platform_entries(source))
            except Exception as exc:
                self.events.put(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        if not self.window.winfo_exists():
            return
        try:
            result = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.analyze.configure(state="normal")
            if isinstance(result, str):
                self.status.set(result)
            else:
                self.entries = result  # type: ignore[assignment]
                self.list.delete(0, "end")
                for title, _url in self.entries:
                    self.list.insert("end", title)
                self.list.select_set(0, "end")
                self.status.set(f"共 {len(self.entries)} 项，按住 Ctrl 可选择多项。")
        self.window.after(150, self._poll)

    def _add(self) -> None:
        selected = [self.entries[index][1] for index in self.list.curselection()]
        if selected:
            self.on_add(selected)
            self.window.destroy()
