"""Non-blocking network configuration and conservative connection diagnostics."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from .errors import LocalizerError
from .network import (
    NetworkSettings,
    load_network_settings,
    resolve_route,
    save_network_settings,
    test_connection,
)

MODES = {"跟随系统（固定当前代理）": "system", "直接连接": "direct", "手动代理": "manual"}


class NetworkDialog:
    def __init__(
        self,
        parent: tk.Misc,
        *,
        target: str,
        can_apply: Callable[[], bool],
        on_apply: Callable[[], None],
    ) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title("网络与连接")
        self.window.geometry("740x620")
        self.window.minsize(650, 600)
        self.window.transient(parent)
        self.can_apply, self.on_apply = can_apply, on_apply
        self.events: queue.Queue[str] = queue.Queue()
        self.testing = False
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(
            body, text="登录、解析、下载，共用一条线路", font=("Microsoft YaHei UI", 14, "bold")
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))
        try:
            settings = load_network_settings()
        except LocalizerError:
            settings = NetworkSettings()
        self.mode = tk.StringVar(value=next(k for k, v in MODES.items() if v == settings.mode))
        self.proxy = tk.StringVar(value=settings.proxy)
        self.target = tk.StringVar(
            value=target
            if target.startswith(("http://", "https://"))
            else "https://www.youtube.com/"
        )
        ttk.Label(body, text="线路模式").grid(row=1, column=0, sticky="w")
        combo = ttk.Combobox(body, textvariable=self.mode, values=tuple(MODES), state="readonly")
        combo.grid(row=2, column=0, sticky="ew", pady=(4, 12))
        combo.bind("<<ComboboxSelected>>", lambda _: self._update_mode())
        ttk.Label(body, text="代理地址（使用已有代理客户端的固定端口）").grid(
            row=3, column=0, sticky="w"
        )
        self.proxy_entry = ttk.Entry(body, textvariable=self.proxy)
        self.proxy_entry.grid(row=4, column=0, sticky="ew", pady=(4, 8))
        ttk.Label(
            body,
            text="例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080。\n"
            "不保存代理账号密码；PAC 自动脚本请改填固定端口。SOCKS5/HTTPS 代理不支持远程片段预览，请用本地视频或 HTTP 代理端口。",
            wraplength=680,
            foreground="#69717D",
        ).grid(row=5, column=0, sticky="w", pady=(0, 12))
        ttk.Label(body, text="测试网站 / 视频地址（仅请求响应头，不下载视频）").grid(
            row=6, column=0, sticky="w"
        )
        ttk.Entry(body, textvariable=self.target).grid(row=7, column=0, sticky="ew", pady=(4, 10))
        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, sticky="w")
        self.test_button = ttk.Button(buttons, text="测试所选线路", command=self._test)
        self.test_button.pack(side="left")
        ttk.Button(buttons, text="保存并应用", command=self._apply).pack(side="left", padx=8)
        self.result = tk.StringVar(
            value="连接诊断会区分限流、连接超时、DNS、地区提示与登录权限；HTTP 403 不等于 IP 被封。"
        )
        ttk.Label(body, textvariable=self.result, wraplength=680, justify="left").grid(
            row=9, column=0, sticky="nw", pady=16
        )
        body.rowconfigure(9, weight=1)
        ttk.Label(
            body,
            text="切换线路前请结束下载、浏览器抓取和平台登录。应用后会重新解析队列；绑定旧 IP 的直链请重新抓取，账号会话必要时重新登录。\n"
            "仅影响媒体连接：本地 AI、浏览器控制、软件更新不走此设置。软件不提供代理服务，不绕过账号、付费、地区法律或 DRM 权限。",
            wraplength=680,
            foreground="#69717D",
        ).grid(row=10, column=0, sticky="sw")
        self._update_mode()
        self.window.after(100, self._poll)

    def _update_mode(self) -> None:
        self.proxy_entry.configure(
            state="normal" if MODES[self.mode.get()] == "manual" else "disabled"
        )

    def _settings(self) -> NetworkSettings:
        return NetworkSettings(MODES[self.mode.get()], self.proxy.get()).validated()

    def _test(self) -> None:
        if self.testing:
            return
        try:
            route = resolve_route(self._settings())
        except (OSError, ValueError, LocalizerError) as exc:
            self.result.set(str(exc))
            return
        target = self.target.get().strip()
        self.testing = True
        self.test_button.configure(state="disabled")
        self.result.set("正在测试所选线路；本次测试不会自动保存设置或改变正在运行的任务…")

        def worker() -> None:
            try:
                result = test_connection(target, route)
            except (OSError, ValueError, LocalizerError, ImportError):
                result = "无法完成测试，请检查测试地址、代理配置和 SOCKS5 组件。"
            self.events.put(result)

        threading.Thread(target=worker, daemon=True, name="localizer-network-test").start()

    def _poll(self) -> None:
        if not self.window.winfo_exists():
            return
        try:
            result = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.testing = False
            self.test_button.configure(state="normal")
            self.result.set(result)
        self.window.after(100, self._poll)

    def _apply(self) -> None:
        if not self.can_apply():
            messagebox.showinfo(
                "请先结束当前连接",
                "请等待视频解析结束，并停止下载、关闭浏览器抓取与平台账号窗口，再应用线路。",
                parent=self.window,
            )
            return
        try:
            route = save_network_settings(self._settings())
        except (OSError, ValueError, LocalizerError) as exc:
            self.result.set(str(exc))
            return
        self.result.set(f"已应用：{route.label}。队列将重新解析；IP 绑定的链接需重新抓取。")
        self.on_apply()
