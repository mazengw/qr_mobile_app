"""Visual theme for QR Vault mobile client."""

from dataclasses import dataclass

import flet as ft


@dataclass(frozen=True)
class Colors:
    bg: str = "#0B1220"
    surface: str = "#121A2B"
    surface_alt: str = "#1A2438"
    border: str = "#2A3650"
    primary: str = "#14B8A6"
    primary_dim: str = "#0F766E"
    accent: str = "#38BDF8"
    text: str = "#F8FAFC"
    text_muted: str = "#94A3B8"
    danger: str = "#F43F5E"
    warning: str = "#F59E0B"
    success: str = "#22C55E"
    owned: str = "#14B8A6"
    shared: str = "#38BDF8"


C = Colors()


def page_theme() -> ft.Theme:
    return ft.Theme(
        color_scheme_seed=C.primary,
        visual_density=ft.VisualDensity.COMFORTABLE,
    )


def card(content: ft.Control, padding: int = 16) -> ft.Container:
    return ft.Container(
        content=content,
        bgcolor=C.surface,
        border=ft.Border.all(1, C.border),
        border_radius=18,
        padding=padding,
        shadow=ft.BoxShadow(
            blur_radius=18,
            color="#00000055",
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
            color=C.bg,
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
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=C.bg),
        bgcolor=color,
        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
        border_radius=999,
    )
