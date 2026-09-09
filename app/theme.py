"""Visual theme for QR Vault mobile client (dark + light)."""

from __future__ import annotations

from typing import Any

import flet as ft

THEME_DARK = "dark"
THEME_LIGHT = "light"


DARK_TOKENS: dict[str, Any] = {
    "mode": THEME_DARK,
    "bg": "#0B1220",
    "surface": "#121A2B",
    "surface_alt": "#1A2438",
    "border": "#2A3650",
    "primary": "#14B8A6",
    "primary_dim": "#0F766E",
    "accent": "#38BDF8",
    "text": "#F8FAFC",
    "text_muted": "#94A3B8",
    "danger": "#F43F5E",
    "warning": "#F59E0B",
    "success": "#22C55E",
    "owned": "#14B8A6",
    "shared": "#38BDF8",
    # Text/icons on primary-colored fills (teal / accent chips)
    "on_primary": "#0B1220",
    "shadow": "#00000055",
    "gradient": ("#0B1220", "#0F172A", "#042F2E"),
    "scrim": "#55000000",
    "image_placeholder": "#0B1220",
    "image_placeholder_icon": "#FFFFFF88",
}


LIGHT_TOKENS: dict[str, Any] = {
    "mode": THEME_LIGHT,
    "bg": "#F1F5F9",
    "surface": "#FFFFFF",
    "surface_alt": "#E2E8F0",
    "border": "#CBD5E1",
    "primary": "#0D9488",
    "primary_dim": "#0F766E",
    "accent": "#0284C7",
    "text": "#0F172A",
    "text_muted": "#64748B",
    "danger": "#E11D48",
    "warning": "#D97706",
    "success": "#16A34A",
    "owned": "#0D9488",
    "shared": "#0284C7",
    "on_primary": "#FFFFFF",
    "shadow": "#0F172A18",
    "gradient": ("#F8FAFC", "#F1F5F9", "#E0F2F1"),
    "scrim": "#33000000",
    "image_placeholder": "#E2E8F0",
    "image_placeholder_icon": "#64748B",
}


class _ThemeTokens:
    """Mutable live palette; attributes updated by apply_theme()."""

    def __getattr__(self, name: str):
        raise AttributeError(name)


C = _ThemeTokens()


def normalize_theme(mode: str | None) -> str:
    return THEME_LIGHT if str(mode or "").strip().lower() == THEME_LIGHT else THEME_DARK


def apply_theme(mode: str | None) -> str:
    """Apply dark/light tokens onto module-level C. Returns normalized mode."""
    key = normalize_theme(mode)
    tokens = LIGHT_TOKENS if key == THEME_LIGHT else DARK_TOKENS
    for name, value in tokens.items():
        setattr(C, name, value)
    return key


# Default until Session loads the user's preference.
apply_theme(THEME_DARK)


def page_theme() -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=C.primary,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )


def flet_theme_mode() -> ft.ThemeMode:
    return ft.ThemeMode.LIGHT if C.mode == THEME_LIGHT else ft.ThemeMode.DARK


def card(content: ft.Control, padding: int = 16) -> ft.Container:
    return ft.Container(
        content=content,
        bgcolor=C.surface,
        border=ft.Border.all(1, C.border),
        border_radius=18,
        padding=padding,
        shadow=ft.BoxShadow(
            blur_radius=18,
            color=C.shadow,
            offset=ft.Offset(0, 8),
        ),
    )


def primary_button(text: str, on_click, icon=None, expand=True) -> ft.Control:
    return ft.Button(
        content=text,
        icon=icon,
        on_click=on_click,
        expand=expand,
        style=ft.ButtonStyle(
            bgcolor=C.primary,
            color=C.on_primary,
            padding=16,
            shape=ft.RoundedRectangleBorder(radius=14),
        ),
    )


def ghost_button(text: str, on_click, icon=None, expand: bool = False) -> ft.Control:
    return ft.OutlinedButton(
        content=text,
        icon=icon,
        on_click=on_click,
        expand=expand,
        style=ft.ButtonStyle(
            color=C.text,
            side=ft.BorderSide(1, C.border),
            padding=14,
            shape=ft.RoundedRectangleBorder(radius=14),
        ),
    )


def section_title(text: str) -> ft.Text:
    return ft.Text(text, size=20, weight=ft.FontWeight.W_700, color=C.text)


def muted(text: str, size: int = 13) -> ft.Text:
    return ft.Text(text, size=size, color=C.text_muted)


def chip(text: str, color: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=C.on_primary),
        bgcolor=color,
        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
        border_radius=999,
    )
