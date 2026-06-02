from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from mkdocs.config import config_options
from mkdocs.exceptions import PluginError
from mkdocs.plugins import BasePlugin
from mkdocs.structure.pages import Page
from mkdocs.utils import get_relative_url


IMAGE_PATTERN = re.compile(r"!\[(?P<alt>h5p)(?P<title>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
SHORTCODE_PATTERN = re.compile(r"\{\{\s*h5p\(\s*[\"'](?P<path>[^\"']+)[\"']\s*\)\s*\}\}")
SAFE_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class H5PAsset:
    source: Path
    output_name: str


class H5PPlugin(BasePlugin):
    config_scheme = (
        ("enabled", config_options.Type(bool, default=True)),
        ("h5p_dir", config_options.Type(str, default="assets/h5p")),
        (
            "player_url",
            config_options.Type(
                str,
                default="https://cdn.jsdelivr.net/npm/h5p-standalone@3.8.0/dist",
            ),
        ),
        ("frame", config_options.Type(bool, default=True)),
        ("full_screen", config_options.Type(bool, default=True)),
        ("export", config_options.Type(bool, default=False)),
        ("embed", config_options.Type(bool, default=False)),
        ("copyright", config_options.Type(bool, default=False)),
        ("player_options", config_options.Type(dict, default={})),
    )

    def on_config(self, config: dict[str, Any]) -> dict[str, Any]:
        self.docs_dir = Path(config["docs_dir"]).resolve()
        self.site_dir = Path(config["site_dir"]).resolve()
        self._page_counter = 0
        return config

    def on_page_markdown(
        self,
        markdown: str,
        *,
        page: Page,
        config: dict[str, Any],
        files: Any,
    ) -> str:
        if not self.config["enabled"]:
            return markdown

        players: list[dict[str, Any]] = []

        def replace_image(match: re.Match[str]) -> str:
            title = match.group("title").strip() or None
            return self._render_player(match.group("path"), page, title, players)

        def replace_shortcode(match: re.Match[str]) -> str:
            return self._render_player(match.group("path"), page, None, players)

        markdown = IMAGE_PATTERN.sub(replace_image, markdown)
        markdown = SHORTCODE_PATTERN.sub(replace_shortcode, markdown)

        if not players:
            return markdown

        return f"{markdown}\n\n{self._render_loader(players, page)}"

    def _render_player(
        self,
        raw_path: str,
        page: Page,
        title: str | None,
        players: list[dict[str, Any]],
    ) -> str:
        source = self._resolve_h5p_path(raw_path, page)
        asset = self._extract_h5p(source)
        h5p_json_path = self._page_relative_url(page, self._asset_url(asset.output_name))
        player_id = self._player_id(source)
        label = html.escape(title or source.stem)

        options = {
            **self.config["player_options"],
            "id": player_id,
            "h5pJsonPath": h5p_json_path,
            "frameJs": self._page_relative_url(page, self._player_asset_url("frame.bundle.js")),
            "frameCss": self._page_relative_url(page, self._player_asset_url("styles/h5p.css")),
            "frame": self.config["frame"],
            "fullScreen": self.config["full_screen"],
            "export": self.config["export"],
            "embed": self.config["embed"],
            "copyright": self.config["copyright"],
        }

        players.append({"id": player_id, "options": options})
        return f'<div id="{player_id}" class="mkdocs-h5p" aria-label="{label}"></div>'

    def _resolve_h5p_path(self, raw_path: str, page: Page) -> Path:
        raw_path = raw_path.split("#", 1)[0].split("?", 1)[0]
        path = Path(raw_path)
        candidates = []

        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(Path(page.file.abs_src_path).parent / path)
            candidates.append(self.docs_dir / path)

        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_file():
                if resolved.suffix.lower() != ".h5p":
                    raise PluginError(f"H5P reference is not a .h5p file: {raw_path}")
                return resolved

        raise PluginError(f"H5P file not found: {raw_path}")

    def _extract_h5p(self, source: Path) -> H5PAsset:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
        output_name = f"{_slug(source.stem)}-{digest}"
        destination = self.site_dir / self.config["h5p_dir"] / output_name

        if not destination.exists():
            destination.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise PluginError(f"Unsafe path in H5P archive {source}: {member.filename}")
                    target = destination / member_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

        return H5PAsset(source=source, output_name=output_name)

    def _render_loader(self, players: list[dict[str, Any]], page: Page) -> str:
        main_bundle = self._page_relative_url(page, self._player_asset_url("main.bundle.js"))
        payload = json.dumps(players, separators=(",", ":")).replace("</", "<\\/")
        return (
            f'<script src="{html.escape(main_bundle)}" charset="UTF-8"></script>\n'
            "<script>\n"
            "(function(){\n"
            f"  var players = {payload};\n"
            "  function init(){\n"
            "    if (!window.H5PStandalone || !window.H5PStandalone.H5P) {\n"
            "      console.error('mkdocs-h5p: h5p-standalone failed to load');\n"
            "      return;\n"
            "    }\n"
            "    (async function(){\n"
            "      for (var index = 0; index < players.length; index += 1) {\n"
            "        var player = players[index];\n"
            "        var element = document.getElementById(player.id);\n"
            "        if (!element) continue;\n"
            "        try {\n"
            "          await new window.H5PStandalone.H5P(element, player.options);\n"
            "        } catch (error) {\n"
            "          console.error('mkdocs-h5p: failed to initialize H5P player', player.id, error);\n"
            "        }\n"
            "      }\n"
            "    }());\n"
            "  }\n"
            "  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);\n"
            "  else init();\n"
            "}());\n"
            "</script>"
        )

    def _asset_url(self, output_name: str) -> str:
        return _join_url(self.config["h5p_dir"], output_name)

    def _player_asset_url(self, filename: str) -> str:
        return _join_url(self.config["player_url"], filename)

    def _page_relative_url(self, page: Page, target: str) -> str:
        if _is_absolute_url(target) or target.startswith("/"):
            return target
        return get_relative_url(target, page.url)

    def _player_id(self, source: Path) -> str:
        self._page_counter += 1
        seed = f"{source}:{self._page_counter}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
        return f"mkdocs-h5p-{digest}"


def _slug(value: str) -> str:
    slug = SAFE_ID_PATTERN.sub("-", value.strip()).strip("-").lower()
    return slug or "content"


def _join_url(base: str, path: str) -> str:
    if _is_absolute_url(base):
        return f"{base.rstrip('/')}/{quote(path, safe='/')}"
    return posixpath.join(base.rstrip("/"), quote(path, safe="/"))


def _is_absolute_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "//"))
