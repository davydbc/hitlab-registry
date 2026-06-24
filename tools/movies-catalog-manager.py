#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HitLab Films Catalogue Manager

Desktop application for building and maintaining the HitLab movie catalogue.

The application integrates with The Movie Database (TMDB) API to search and browse movies, and generates JSON catalogue
files used by the game. It also connects to the Spotify Web API, providing features such as soundtrack and song searches,
playlist creation, and music-related catalogue management.

Main features:
- Search and discover movies from TMDB.
- Create and maintain the HitLab movie catalogue.
- Export catalogue data to JSON format.
- Search songs and soundtracks on Spotify.
- Create and manage Spotify playlists.
- Assist in selecting and validating music associated with movies.

This tool is designed to simplify the creation and curation of movie and soundtrack metadata for the HitLab game.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests
import unicodedata
from PIL import Image, UnidentifiedImageError
from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

APP_DIR = Path(__file__).resolve().parent / "target" / "movies-catalog-manager"
FILMS_FILE = APP_DIR / "movies.json"
GENRES_FILE = APP_DIR / "genres.json"
FILMS_CACHE_DIR = APP_DIR / "films_cache"
POSTER_CACHE_DIR = APP_DIR / "poster_cache"
PENDING_SOUNDTRACK_RECOMMENDATIONS_FILE = APP_DIR / "pending_soundtrack_recommendations.md"

TMDB_BEARER_TOKEN = "<TMDB_BEARER_TOKEN>"
TMDB_API_KEY = "<TMDB_API_KEY>"
TMDB_BASE_URL = "https://api.themoviedb.org/3"
POSTER_BASE_URL = "https://image.tmdb.org/t/p/w500"
LANGUAGE = "es-ES"
REGION = "ES"
INCLUDE_ADULT = False
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_SLEEP_SECONDS = 0.05
MAX_RETRIES = 4

SORTS = [
    "popularity.desc",
    "vote_count.desc",
    "vote_average.desc",
    "revenue.desc",
]

LIMIT_OPTIONS = [100, 250, 500, 1000, 2000, 5000]
ROWS_PER_PAGE = 50
MIN_YEAR = 1900
CURRENT_YEAR = date.today().year
POSTER_CACHE_SIZE = (184, 276)
POSTER_DISPLAY_SIZE = (152, 216)
POSTER_CELL_SIZE = (164, 226)

SPOTIFY_CLIENT_ID = "<SPOTIFY_CLIENT_ID>"
SPOTIFY_CLIENT_SECRET = "<SPOTIFY_CLIENT_SECRET>"

SPOTIFY_TRACK_URI = "spotify:track:{spotify_id}"
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SPOTIFY_PLAYLIST_SCOPES = ("playlist-modify-private", "playlist-modify-public", "user-read-private")
SPOTIFY_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{22}$")
SPOTIFY_EMPTY_VALUES = {"", "<pending>", "pending", "none", "null", "nan"}
SPOTIFY_TRACK_SEARCH_LIMIT = 20

RATING_WEIGHT = 1.0
VOTES_WEIGHT = 1.0
POPULARITY_WEIGHT = 1.5
CREDITS_SELECTION_VERSION = 2

COLUMNS = [
    "Select",
    "Rank",
    "Poster",
    "Title",
    "Original",
    "Year",
    "Difficulty",
    "Relevance",
    "Genres",
    "Director",
    "Actor/Actress",
    "Song",
    "Song Author",
    "Spotify ID",
    "Spotify",
    "Score",
    "Rating",
    "Votes",
    "Popularity",
    "Imported",
]

COL_SELECT = 0
COL_RANK = 1
COL_POSTER = 2
COL_TITLE = 3
COL_ORIGINAL = 4
COL_YEAR = 5
COL_DIFFICULTY = 6
COL_RELEVANCE = 7
COL_GENRES = 8
COL_DIRECTOR = 9
COL_KNOWN_ACTOR = 10
COL_SONG = 11
COL_SONG_AUTHOR = 12
COL_SPOTIFY_ID = 13
COL_SPOTIFY_PLAY = 14
COL_SCORE = 15
COL_RATING = 16
COL_VOTES = 17
COL_POPULARITY = 18
COL_IMPORTED = 19

EDITABLE_MUSIC_COLUMNS = (COL_SONG, COL_SONG_AUTHOR, COL_SPOTIFY_ID)
EDITABLE_RATING_COLUMNS = (COL_DIFFICULTY, COL_RELEVANCE)


class AppError(Exception):
    """Error safe to show in the UI."""


def response_wait_time(response: requests.Response) -> str:
    retry_after = clean_edit_value(response.headers.get("Retry-After"))
    rate_limit_reset = clean_edit_value(response.headers.get("X-RateLimit-Reset"))
    details = []
    if retry_after:
        details.append(f"Retry-After={retry_after}")
    if rate_limit_reset:
        details.append(f"X-RateLimit-Reset={rate_limit_reset}")
    return ", ".join(details)


def retry_after_sleep_seconds(response: requests.Response, default: int = 2) -> int:
    retry_after = clean_edit_value(response.headers.get("Retry-After"))
    try:
        return max(0, int(float(retry_after)))
    except (TypeError, ValueError):
        return default


def trace_api_wait(api_name: str, response: requests.Response) -> None:
    wait_time = response_wait_time(response)
    if not wait_time:
        wait_time = "sin cabecera de espera"
    print(
        f"[{api_name}] HTTP {response.status_code}; tiempo de espera recibido: {wait_time}",
        file=sys.stderr,
        flush=True,
    )


def trace_exception(context: str, exc: BaseException) -> None:
    print(f"[ERROR] {context}: {exc}", file=sys.stderr, flush=True)
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)


@dataclass(frozen=True)
class SearchParams:
    source: str
    year_from: int
    year_to: int
    genres: tuple[int, ...]
    limit: int
    title_filter: str = ""
    difficulty: int = 0
    relevance: int = 0
    language: str = LANGUAGE
    region: str = REGION
    sorts: tuple[str, ...] = tuple(SORTS)


def ensure_dirs() -> None:
    FILMS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    POSTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def sorted_films(films: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(films, key=lambda x: str(x.get("title") or "").lower())


def load_films() -> list[dict[str, Any]]:
    data = load_json(FILMS_FILE, [])
    if not isinstance(data, list):
        return []
    return data


def save_films(films: list[dict[str, Any]]) -> None:
    atomic_write_json(FILMS_FILE, sorted_films(films))


def load_genres() -> dict[str, str]:
    data = load_json(GENRES_FILE, {})
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def save_genres(genres: dict[str, str]) -> None:
    ordered = dict(sorted(genres.items(), key=lambda item: int(item[0])))
    atomic_write_json(GENRES_FILE, ordered)


def year_from_date(raw_date: Any) -> int | None:
    if not raw_date:
        return None
    try:
        return int(str(raw_date)[:4])
    except ValueError:
        return None


def score_item(item: dict[str, Any]) -> float:
    vote_average = float(item.get("vote_average") or 0)
    vote_count = int(item.get("vote_count") or 0)
    popularity = float(item.get("popularity") or 0)
    return (
            RATING_WEIGHT * vote_average
            + VOTES_WEIGHT * math.log10(vote_count + 1)
            + POPULARITY_WEIGHT * math.log10(popularity + 1)
    )


def normalize_movie(item: dict[str, Any]) -> dict[str, Any]:
    raw_date = item.get("release_date") or None
    poster_path = item.get("poster_path")
    title = item.get("title") or item.get("original_title") or ""
    original_title = item.get("original_title") or title
    return {
        "id": str(item.get("id")),
        "tmdb_type": "movie",
        "type": 1,
        "poster": f"{POSTER_BASE_URL}{poster_path}" if poster_path else None,
        "title": title,
        "original_title": original_title,
        "year": year_from_date(raw_date),
        "date": raw_date,
        "genres": [int(g) for g in item.get("genre_ids", [])],
        "score": round(score_item(item), 4),
        "tmdb_vote_average": item.get("vote_average") or 0,
        "tmdb_vote_count": item.get("vote_count") or 0,
        "tmdb_popularity": item.get("popularity") or 0,
        "director": clean_edit_value(item.get("director")),
        "known_actor": clean_edit_value(item.get("known_actor")),
        "credits_selection_version": int(item.get("credits_selection_version") or 0),
    }


def cast_popularity(person: dict[str, Any]) -> float:
    try:
        return float(person.get("popularity") or 0)
    except (TypeError, ValueError):
        return 0.0


def representative_cast_names(cast: list[dict[str, Any]]) -> str:
    if not cast:
        return ""
    selected = [clean_edit_value(cast[0].get("name"))]
    for index in range(1, min(len(cast), 3)):
        if cast_popularity(cast[index]) > cast_popularity(cast[index - 1]):
            selected.append(clean_edit_value(cast[index].get("name")))
    return ", ".join(dict.fromkeys(name for name in selected if name))


def credits_names(credits: dict[str, Any]) -> tuple[str, str]:
    directors = [
        clean_edit_value(person.get("name"))
        for person in credits.get("crew", [])
        if isinstance(person, dict)
           and clean_edit_value(person.get("job")).casefold() == "director"
           and has_defined_text(person.get("name"))
    ]
    cast = [
        person
        for person in credits.get("cast", [])
        if isinstance(person, dict) and has_defined_text(person.get("name"))
    ]
    known_actor = representative_cast_names(cast)
    return ", ".join(dict.fromkeys(directors)), known_actor


def display_float(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def normalized_rating_value(value: Any) -> int | None:
    try:
        rating = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= rating <= 5:
        return rating
    return None


def display_rating_value(value: Any) -> str:
    rating = normalized_rating_value(value)
    return str(rating) if rating is not None else ""


def clean_edit_value(value: Any) -> str:
    return str(value or "").strip()


def has_defined_text(value: Any) -> bool:
    text = clean_edit_value(value)
    return text.casefold() not in SPOTIFY_EMPTY_VALUES


def has_complete_song_info(film: dict[str, Any]) -> bool:
    return has_defined_text(film.get("song")) and has_defined_text(film.get("song_author"))


def credits_selection_version(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def needs_credits_update(film: dict[str, Any]) -> bool:
    return (
            not has_defined_text(film.get("director"))
            or not has_defined_text(film.get("known_actor"))
            or credits_selection_version(film.get("credits_selection_version")) < CREDITS_SELECTION_VERSION
    )


def valid_spotify_id(value: Any) -> bool:
    text = clean_edit_value(value)
    if text.casefold() in SPOTIFY_EMPTY_VALUES:
        return False
    return bool(SPOTIFY_ID_PATTERN.fullmatch(text))


def sanitize_spotify_track_id(value: Any) -> str:
    text = clean_edit_value(value)
    if not text:
        return ""
    match = re.search(r"(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?track/|spotify:track:)([A-Za-z0-9]{22})", text)
    if match:
        return match.group(1)
    track_segment = re.search(r"(?:/track/|spotify:track:)([^/?#&\s]+)", text)
    if track_segment:
        return track_segment.group(1).strip()
    return text.split("?", 1)[0].split("&", 1)[0].split("#", 1)[0].strip()


def spotify_track_uri(spotify_id: Any) -> str | None:
    text = sanitize_spotify_track_id(spotify_id)
    if not valid_spotify_id(text):
        return None
    return SPOTIFY_TRACK_URI.format(spotify_id=text)


def spotify_search_query(value: Any) -> str:
    text = clean_edit_value(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\[\]{}()<>:\"'`^~*?\\|/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def spotify_error_detail(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return clean_edit_value(response.text)[:300]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        message = clean_edit_value(error.get("message"))
        status = clean_edit_value(error.get("status"))
        return f"{status} {message}".strip()
    if isinstance(error, str):
        return error
    return clean_edit_value(data)[:300]


class SpotifyClient:
    def __init__(self) -> None:
        self.client_id = SPOTIFY_CLIENT_ID
        self.client_secret = SPOTIFY_CLIENT_SECRET
        self.access_token: str | None = None
        self.expires_at = 0.0
        self.user_access_token: str | None = None
        self.user_expires_at = 0.0
        if not self.client_id or not self.client_secret:
            raise AppError("Faltan credenciales de Spotify.")

    def token(self) -> str:
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token
        response = requests.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            trace_api_wait("Spotify auth", response)
            raise AppError(f"Error HTTP {response.status_code} autenticando en Spotify.")
        try:
            data = response.json()
        except ValueError as exc:
            raise AppError("Spotify devolvio una respuesta de autenticacion invalida.") from exc
        token = clean_edit_value(data.get("access_token"))
        if not token:
            raise AppError("Spotify no devolvio token de acceso.")
        self.access_token = token
        self.expires_at = time.time() + int(data.get("expires_in") or 3600)
        return token

    def request_user_token(self) -> str:
        if self.user_access_token and time.time() < self.user_expires_at - 60:
            return self.user_access_token

        redirect = urlparse(SPOTIFY_REDIRECT_URI)
        if redirect.scheme != "http" or redirect.hostname != "127.0.0.1":
            raise AppError("SPOTIFY_REDIRECT_URI debe ser una URL loopback http, por ejemplo http://127.0.0.1:8888/callback.")
        if redirect.port is None:
            raise AppError("SPOTIFY_REDIRECT_URI debe incluir puerto, por ejemplo http://127.0.0.1:8888/callback.")

        state = secrets.token_urlsafe(24)
        callback_path = redirect.path or "/callback"
        received = threading.Event()
        result: dict[str, str] = {}

        class OAuthCallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != callback_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(parsed.query)
                result["code"] = clean_edit_value(query.get("code", [""])[0])
                result["state"] = clean_edit_value(query.get("state", [""])[0])
                result["error"] = clean_edit_value(query.get("error", [""])[0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>Spotify autorizado</h1>"
                    b"<p>Puede volver a la aplicacion.</p></body></html>"
                )
                received.set()

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        try:
            server = HTTPServer((redirect.hostname, redirect.port), OAuthCallbackHandler)
        except OSError as exc:
            raise AppError(f"No se pudo abrir el callback local de Spotify en {redirect.hostname}:{redirect.port}.") from exc

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        auth_url = (
            f"{SPOTIFY_AUTH_URL}?"
            + urlencode(
                {
                    "client_id": self.client_id,
                    "response_type": "code",
                    "redirect_uri": SPOTIFY_REDIRECT_URI,
                    "scope": " ".join(SPOTIFY_PLAYLIST_SCOPES),
                    "state": state,
                    "show_dialog": "true",
                }
            )
        )
        QDesktopServices.openUrl(QUrl(auth_url))

        deadline = time.time() + 180
        while time.time() < deadline and not received.is_set():
            QApplication.processEvents()
            time.sleep(0.1)
        server.shutdown()
        server.server_close()

        if not received.is_set():
            raise AppError(
                "No se recibio autorizacion de Spotify antes de agotar el tiempo de espera. "
                f"Compruebe que {SPOTIFY_REDIRECT_URI} esta registrada exactamente como Redirect URI en la app de Spotify."
            )
        if result.get("error"):
            raise AppError(f"Spotify rechazo la autorizacion: {result['error']}")
        if result.get("state") != state:
            raise AppError("La respuesta de Spotify no coincide con la solicitud original.")
        code = clean_edit_value(result.get("code"))
        if not code:
            raise AppError("Spotify no devolvio codigo de autorizacion.")

        response = requests.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": SPOTIFY_REDIRECT_URI,
            },
            auth=(self.client_id, self.client_secret),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            trace_api_wait("Spotify user auth", response)
            detail = spotify_error_detail(response)
            suffix = f" Detalle: {detail}" if detail else ""
            raise AppError(f"Error HTTP {response.status_code} autenticando usuario de Spotify.{suffix}")
        try:
            data = response.json()
        except ValueError as exc:
            raise AppError("Spotify devolvio una respuesta de autenticacion de usuario invalida.") from exc
        token = clean_edit_value(data.get("access_token"))
        if not token:
            raise AppError("Spotify no devolvio token de usuario.")
        self.user_access_token = token
        self.user_expires_at = time.time() + int(data.get("expires_in") or 3600)
        return token

    def user_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.request_user_token()}"}

    def current_user_id(self) -> str:
        response = requests.get(
            f"{SPOTIFY_API_BASE_URL}/me",
            headers=self.user_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            trace_api_wait("Spotify me", response)
            raise AppError(f"Error HTTP {response.status_code} consultando el usuario de Spotify.")
        try:
            data = response.json()
        except ValueError as exc:
            raise AppError("Spotify devolvio una respuesta de usuario invalida.") from exc
        user_id = clean_edit_value(data.get("id"))
        if not user_id:
            raise AppError("Spotify no devolvio el identificador del usuario.")
        return user_id

    def create_playlist(self, name: str, spotify_ids: list[str]) -> dict[str, str]:
        clean_name = clean_edit_value(name)
        if not clean_name:
            raise AppError("El nombre de la lista no puede estar vacio.")
        track_uris = [
            SPOTIFY_TRACK_URI.format(spotify_id=spotify_id)
            for spotify_id in dict.fromkeys(sanitize_spotify_track_id(value) for value in spotify_ids)
            if valid_spotify_id(spotify_id)
        ]
        if not track_uris:
            raise AppError("No hay canciones con Spotify ID valido para crear la lista.")

        response = requests.post(
            f"{SPOTIFY_API_BASE_URL}/me/playlists",
            headers={**self.user_headers(), "Content-Type": "application/json"},
            json={"name": clean_name, "public": False},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            trace_api_wait("Spotify playlist", response)
            detail = spotify_error_detail(response)
            suffix = f" Detalle: {detail}" if detail else ""
            if response.status_code == 403:
                try:
                    user_id = self.current_user_id()
                except AppError:
                    user_id = ""
                if user_id:
                    suffix = (
                        f"{suffix} Usuario autorizado: {user_id}. "
                        "Si la app esta en Development Mode, anada este usuario en Users and Access."
                    )
            raise AppError(f"Error HTTP {response.status_code} creando la lista en Spotify.{suffix}")
        try:
            playlist = response.json()
        except ValueError as exc:
            raise AppError("Spotify devolvio una respuesta de playlist invalida.") from exc
        playlist_id = clean_edit_value(playlist.get("id"))
        playlist_url = clean_edit_value((playlist.get("external_urls") or {}).get("spotify"))
        if not playlist_id:
            raise AppError("Spotify no devolvio el identificador de la playlist.")

        for start in range(0, len(track_uris), 100):
            chunk = track_uris[start:start + 100]
            add_response = requests.post(
                f"{SPOTIFY_API_BASE_URL}/playlists/{playlist_id}/items",
                headers={**self.user_headers(), "Content-Type": "application/json"},
                json={"uris": chunk},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if add_response.status_code >= 400:
                trace_api_wait("Spotify playlist tracks", add_response)
                detail = spotify_error_detail(add_response)
                suffix = f" Detalle: {detail}" if detail else ""
                if add_response.status_code == 403:
                    suffix = (
                        f"{suffix} Playlist: {playlist_id}. "
                        "Spotify ha creado la lista, pero ha rechazado modificar sus canciones. "
                        "Revise que el usuario autorizado sea propietario de la lista y que la app tenga "
                        "los permisos playlist-modify-private/playlist-modify-public."
                    )
                raise AppError(f"Error HTTP {add_response.status_code} anadiendo canciones a Spotify.{suffix}")

        return {"id": playlist_id, "url": playlist_url, "count": str(len(track_uris))}

    def track_info(self, spotify_id: str) -> dict[str, str]:
        response = requests.get(
            f"{SPOTIFY_API_BASE_URL}/tracks/{spotify_id}",
            headers={"Authorization": f"Bearer {self.token()}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 404:
            raise AppError("Track no encontrado en Spotify.")
        if response.status_code >= 400:
            trace_api_wait("Spotify track", response)
            raise AppError(f"Error HTTP {response.status_code} consultando Spotify.")
        try:
            data = response.json()
        except ValueError as exc:
            raise AppError("Spotify devolvio una respuesta JSON invalida.") from exc
        song = clean_edit_value(data.get("name"))
        artists = [
            clean_edit_value(artist.get("name"))
            for artist in data.get("artists", [])
            if isinstance(artist, dict) and has_defined_text(artist.get("name"))
        ]
        if not song:
            raise AppError("Spotify no devolvio el nombre de la cancion.")
        if not artists:
            raise AppError("Spotify no devolvio artistas para la cancion.")
        return {"song": song, "song_author": ", ".join(artists)}

    def search_tracks(self, query: str, limit: int = SPOTIFY_TRACK_SEARCH_LIMIT) -> list[dict[str, Any]]:
        query = spotify_search_query(query)
        if not query:
            return []
        try:
            safe_limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            safe_limit = SPOTIFY_TRACK_SEARCH_LIMIT
        response = requests.get(
            f"{SPOTIFY_API_BASE_URL}/search",
            headers={"Authorization": f"Bearer {self.token()}"},
            params={"q": query, "type": "track", "limit": str(safe_limit)},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            trace_api_wait("Spotify search", response)
            detail = spotify_error_detail(response)
            if response.status_code == 400 and "invalid limit" in normalize_search_text(detail):
                response = requests.get(
                    f"{SPOTIFY_API_BASE_URL}/search",
                    headers={"Authorization": f"Bearer {self.token()}"},
                    params={"q": query, "type": "track"},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                if response.status_code >= 400:
                    trace_api_wait("Spotify search", response)
                detail = spotify_error_detail(response) if response.status_code >= 400 else ""
            if response.status_code >= 400:
                suffix = f"\n\nQuery: {query}"
                if detail:
                    suffix = f"\n\nDetalle: {detail}{suffix}"
                raise AppError(f"Error HTTP {response.status_code} buscando en Spotify.{suffix}")
        try:
            data = response.json()
        except ValueError as exc:
            raise AppError("Spotify devolvio una respuesta JSON invalida.") from exc
        items = data.get("tracks", {}).get("items", [])
        if not isinstance(items, list):
            return []
        tracks: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            spotify_id = sanitize_spotify_track_id(item.get("id"))
            song = clean_edit_value(item.get("name"))
            artists = [
                clean_edit_value(artist.get("name"))
                for artist in item.get("artists", [])
                if isinstance(artist, dict) and has_defined_text(artist.get("name"))
            ]
            if not valid_spotify_id(spotify_id) or not song or not artists:
                continue
            album = item.get("album") if isinstance(item.get("album"), dict) else {}
            tracks.append(
                {
                    "spotify_id": spotify_id,
                    "song": song,
                    "song_author": ", ".join(artists),
                    "album": clean_edit_value(album.get("name")),
                    "popularity": int(item.get("popularity") or 0),
                    "uri": clean_edit_value(item.get("uri")) or SPOTIFY_TRACK_URI.format(spotify_id=spotify_id),
                    "recommended": False,
                }
            )
        return tracks[:safe_limit]


def parse_pending_soundtrack_recommendations(path: Path = PENDING_SOUNDTRACK_RECOMMENDATIONS_FILE) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    recommendations: dict[str, list[str]] = {}
    for line in lines:
        text = line.strip()
        if not text.startswith("|") or text.startswith("|---"):
            continue
        columns = [column.strip() for column in text.strip("|").split("|")]
        if len(columns) < 4 or not columns[0].isdigit():
            continue
        film_id = columns[0]
        songs_text = re.sub(r"<br\s*/?>", "\n", columns[3], flags=re.IGNORECASE)
        songs = [
            re.sub(r"\s+", " ", song).strip()
            for song in songs_text.splitlines()
            if song.strip()
        ]
        songs = [
            song
            for song in songs
            if "no encontre opciones" not in normalize_search_text(song)
        ]
        if songs:
            recommendations[film_id] = songs
    return recommendations


def dedupe_spotify_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for track in tracks:
        spotify_id = clean_edit_value(track.get("spotify_id"))
        if not spotify_id or spotify_id in seen:
            continue
        seen.add(spotify_id)
        deduped.append(track)
    return deduped


def normalize_search_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return without_marks.casefold().strip()


def title_matches(film: dict[str, Any], query: str) -> bool:
    needle = normalize_search_text(query)
    if not needle:
        return True
    title = normalize_search_text(film.get("title"))
    original_title = normalize_search_text(film.get("original_title"))
    return needle in title or needle in original_title


def effective_cache_payload(params: SearchParams) -> dict[str, Any]:
    return {
        "cache_version": 6,
        "source": params.source,
        "year_from": params.year_from,
        "year_to": params.year_to,
        "genres": sorted(active_genre_filter(params)),
        "limit": params.limit,
        "title_filter": normalize_search_text(params.title_filter),
        "language": params.language,
        "region": params.region,
        "sorts": list(params.sorts),
    }


def cache_key(params: SearchParams) -> str:
    payload = effective_cache_payload(params)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_matches_title_filter(cached: list[dict[str, Any]], params: SearchParams) -> bool:
    if not params.title_filter.strip():
        return True
    return all(title_matches(item, params.title_filter) for item in cached)


def legacy_cache_keys(params: SearchParams) -> list[str]:
    legacy_payloads = [
        {
            "cache_version": 5,
            "source": params.source,
            "year_from": params.year_from,
            "year_to": params.year_to,
            "genres": sorted(active_genre_filter(params)),
            "limit": params.limit,
            "title_filter": normalize_search_text(params.title_filter),
            "language": params.language,
            "region": params.region,
            "sorts": list(params.sorts),
        },
        {
            "cache_version": 4,
            "source": params.source,
            "year_from": params.year_from,
            "year_to": params.year_to,
            "genres": sorted(active_genre_filter(params)),
            "limit": params.limit,
            "title_filter": normalize_search_text(params.title_filter),
            "language": params.language,
            "region": params.region,
            "sorts": list(params.sorts),
        },
        {
            "cache_version": 3,
            "source": params.source,
            "year_from": params.year_from,
            "year_to": params.year_to,
            "genres": list(params.genres),
            "limit": params.limit,
            "title_filter": normalize_search_text(params.title_filter),
            "language": params.language,
            "region": params.region,
            "sorts": list(params.sorts),
        },
        {
            "cache_version": 2,
            "source": params.source,
            "year_from": params.year_from,
            "year_to": params.year_to,
            "genres": list(params.genres),
            "limit": params.limit,
            "language": params.language,
            "region": params.region,
            "sorts": list(params.sorts),
        },
    ]
    if params.title_filter.strip():
        legacy_payloads = [
            payload
            for payload in legacy_payloads
            if "title_filter" in payload
        ]
    keys: list[str] = []
    for payload in legacy_payloads:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        keys.append(hashlib.sha256(raw.encode("utf-8")).hexdigest())
    return keys


def tmdb_genre_filter(params: SearchParams) -> str | None:
    selected = tuple(sorted(params.genres))
    known_genres = tuple(sorted(int(genre_id) for genre_id in load_genres()))
    if not selected or (known_genres and selected == known_genres):
        return None
    return "|".join(str(genre_id) for genre_id in selected)


def active_genre_filter(params: SearchParams) -> set[int]:
    selected = set(params.genres)
    known_genres = {int(genre_id) for genre_id in load_genres()}
    if not selected or (known_genres and selected == known_genres):
        return set()
    return selected


def matches_year_and_genres(item: dict[str, Any], params: SearchParams) -> bool:
    raw_date = item.get("release_date") or item.get("date")
    year = year_from_date(raw_date) or item.get("year")
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return False
    if not params.year_from <= year_int <= params.year_to:
        return False

    selected_genres = active_genre_filter(params)
    if not selected_genres:
        return True
    raw_genres = item.get("genre_ids", item.get("genres", []))
    item_genres = {int(genre_id) for genre_id in raw_genres}
    return bool(item_genres.intersection(selected_genres))


class TMDBClient:
    def __init__(self) -> None:
        self.bearer_token = TMDB_BEARER_TOKEN
        self.api_key = TMDB_API_KEY
        if not self.bearer_token and not self.api_key:
            raise AppError(
                "Falta la API Key de TMDB. Define TMDB_BEARER_TOKEN o TMDB_API_KEY."
            )

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{TMDB_BASE_URL}{path}"
        request_params = dict(params or {})
        headers = {"accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        else:
            request_params["api_key"] = self.api_key

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(
                    url,
                    params=request_params,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                if response.status_code == 429:
                    trace_api_wait("TMDB", response)
                    time.sleep(retry_after_sleep_seconds(response))
                    continue
                if response.status_code >= 400:
                    trace_api_wait("TMDB", response)
                    raise AppError(f"Error HTTP {response.status_code} consultando TMDB.")
                time.sleep(REQUEST_SLEEP_SECONDS)
                return response.json()
            except AppError:
                raise
            except requests.Timeout as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 8))
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 8))
            except ValueError as exc:
                raise AppError("TMDB devolvió una respuesta JSON inválida.") from exc
        raise AppError("No se pudo completar la consulta a TMDB.") from last_error


def enrich_movies_with_credits(
        movies: list[dict[str, Any]],
        client: TMDBClient,
        progress: Callable[[str], None] | None = None,
) -> bool:
    changed = False
    missing = [
        movie
        for movie in movies
        if needs_credits_update(movie)
    ]
    total = len(missing)
    for index, movie in enumerate(missing, start=1):
        movie_id = clean_edit_value(movie.get("id"))
        if not movie_id:
            continue
        if progress and (index == 1 or index % 25 == 0 or index == total):
            progress(f"Consultando créditos TMDB: {index}/{total}")
        credits = client.get(f"/movie/{movie_id}/credits", {"language": LANGUAGE})
        director, known_actor = credits_names(credits)
        if director and movie.get("director") != director:
            movie["director"] = director
            changed = True
        if known_actor and movie.get("known_actor") != known_actor:
            movie["known_actor"] = known_actor
            changed = True
        if credits_selection_version(movie.get("credits_selection_version")) != CREDITS_SELECTION_VERSION:
            movie["credits_selection_version"] = CREDITS_SELECTION_VERSION
            changed = True
    return changed


def fetch_genres_from_tmdb() -> dict[str, str]:
    client = TMDBClient()
    data = client.get("/genre/movie/list", {"language": LANGUAGE})
    genres = {
        str(item["id"]): str(item["name"])
        for item in data.get("genres", [])
        if "id" in item and "name" in item
    }
    if not genres:
        raise AppError("TMDB no devolvió géneros de películas.")
    save_genres(genres)
    return genres


def discover_movies(
        params: SearchParams,
        progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    ensure_dirs()
    key = cache_key(params)
    cache_path = FILMS_CACHE_DIR / f"{key}.json"
    cached = load_json(cache_path, None)
    if isinstance(cached, list):
        if cache_matches_title_filter(cached, params):
            if progress:
                progress(f"Búsqueda cargada desde caché: {len(cached)} resultados")
            return cached
        if progress:
            progress("Caché ignorada porque no coincide con el texto buscado")

    for legacy_key in legacy_cache_keys(params):
        legacy_path = FILMS_CACHE_DIR / f"{legacy_key}.json"
        if legacy_path == cache_path:
            continue
        cached = load_json(legacy_path, None)
        if isinstance(cached, list):
            if cache_matches_title_filter(cached, params):
                atomic_write_json(cache_path, cached)
                if progress:
                    progress(f"Búsqueda cargada desde caché existente: {len(cached)} resultados")
                return cached
            if progress:
                progress("Caché existente ignorada porque no coincide con el texto buscado")

    client = TMDBClient()
    candidates: dict[str, dict[str, Any]] = {}
    max_pages = max(1, math.ceil(params.limit / 20))
    with_genres = tmdb_genre_filter(params)
    title_query = params.title_filter.strip()

    if title_query:
        if progress:
            progress(f"Buscando título en TMDB: {title_query}")
        for page in range(1, min(max_pages * 4, 500) + 1):
            data = client.get(
                "/search/movie",
                {
                    "language": params.language,
                    "region": params.region,
                    "include_adult": str(INCLUDE_ADULT).lower(),
                    "query": title_query,
                    "page": page,
                },
            )
            for item in data.get("results", []):
                if not matches_year_and_genres(item, params):
                    continue
                if not title_matches(normalize_movie(item), title_query):
                    continue
                movie_id = str(item.get("id"))
                old = candidates.get(movie_id)
                if old is None or score_item(item) > score_item(old):
                    candidates[movie_id] = item

            total_pages = min(int(data.get("total_pages") or 1), 500)
            if page >= total_pages or len(candidates) >= params.limit:
                break

        ranked = sorted(candidates.values(), key=score_item, reverse=True)
        normalized = [normalize_movie(item) for item in ranked[: params.limit]]
        atomic_write_json(cache_path, normalized)
        if progress:
            progress(f"Búsqueda guardada en caché: {len(normalized)} resultados")
        return normalized

    for sort_by in params.sorts:
        if progress:
            progress(f"Consultando TMDB: {sort_by}")
        for page in range(1, min(max_pages, 500) + 1):
            query = {
                "language": params.language,
                "region": params.region,
                "include_adult": str(INCLUDE_ADULT).lower(),
                "primary_release_date.gte": f"{params.year_from}-01-01",
                "primary_release_date.lte": f"{params.year_to}-12-31",
                "sort_by": sort_by,
                "page": page,
            }
            if with_genres:
                query["with_genres"] = with_genres
            data = client.get("/discover/movie", query)
            for item in data.get("results", []):
                movie_id = str(item.get("id"))
                old = candidates.get(movie_id)
                if old is None or score_item(item) > score_item(old):
                    candidates[movie_id] = item

            total_pages = min(int(data.get("total_pages") or 1), 500)
            if page >= total_pages or len(candidates) >= params.limit * 2:
                break

    ranked = sorted(candidates.values(), key=score_item, reverse=True)
    normalized: list[dict[str, Any]] = []
    for item in ranked:
        movie = normalize_movie(item)
        if title_matches(movie, params.title_filter):
            normalized.append(movie)
        if len(normalized) >= params.limit:
            break
    atomic_write_json(cache_path, normalized)
    if progress:
        progress(f"Búsqueda guardada en caché: {len(normalized)} resultados")
    return normalized


def update_missing_local_credits(
        films: list[dict[str, Any]],
        progress: Callable[[str], None] | None = None,
) -> bool:
    missing = [
        film
        for film in films
        if needs_credits_update(film)
    ]
    if not missing:
        return False
    if progress:
        progress(f"Actualizando créditos de films.json: {len(missing)} pendientes")
    client = TMDBClient()
    changed = False
    total = len(missing)
    for index, film in enumerate(missing, start=1):
        movie_id = clean_edit_value(film.get("id"))
        if not movie_id:
            continue
        if progress:
            progress(f"Actualizando créditos de films.json: {index}/{total}")
        credits = client.get(f"/movie/{movie_id}/credits", {"language": LANGUAGE})
        director, known_actor = credits_names(credits)
        film_changed = False
        if director and film.get("director") != director:
            film["director"] = director
            film_changed = True
        if known_actor and film.get("known_actor") != known_actor:
            film["known_actor"] = known_actor
            film_changed = True
        if credits_selection_version(film.get("credits_selection_version")) != CREDITS_SELECTION_VERSION:
            film["credits_selection_version"] = CREDITS_SELECTION_VERSION
            film_changed = True
        if film_changed:
            save_films(films)
            changed = True
    return changed


def local_search(
        params: SearchParams,
        progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    selected_genres = active_genre_filter(params)
    results: list[dict[str, Any]] = []
    films = load_films()
    update_missing_local_credits(films, progress)
    for film in films:
        year = film.get("year")
        try:
            year_int = int(year)
        except (TypeError, ValueError):
            continue
        if not params.year_from <= year_int <= params.year_to:
            continue
        film_genres = {int(g) for g in film.get("genres", [])}
        if selected_genres and not film_genres.intersection(selected_genres):
            continue
        if not title_matches(film, params.title_filter):
            continue
        if params.difficulty and normalized_rating_value(film.get("difficulty")) != params.difficulty:
            continue
        if params.relevance and normalized_rating_value(film.get("relevance")) != params.relevance:
            continue
        results.append(dict(film))
    return sorted(results, key=lambda item: str(item.get("title") or "").lower())


def poster_cache_path(film: dict[str, Any]) -> Path:
    return POSTER_CACHE_DIR / f"{film.get('id')}.jpg"


def ensure_poster(film: dict[str, Any]) -> Path | None:
    ensure_dirs()
    path = poster_cache_path(film)
    if path.exists() and path.stat().st_size > 0:
        try:
            with Image.open(path) as cached:
                if cached.width >= POSTER_DISPLAY_SIZE[0] and cached.height >= POSTER_DISPLAY_SIZE[1]:
                    return path
        except (OSError, UnidentifiedImageError):
            path.unlink(missing_ok=True)

    url = film.get("poster")
    if not url:
        return None
    response = requests.get(str(url), timeout=REQUEST_TIMEOUT_SECONDS)
    if response.status_code >= 400:
        return None
    try:
        image = Image.open(BytesIO(response.content))
        image.thumbnail(POSTER_CACHE_SIZE, Image.Resampling.LANCZOS)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(path, format="JPEG", quality=88)
        return path
    except (OSError, UnidentifiedImageError):
        path.unlink(missing_ok=True)
        return None


class SearchWorker(QThread):
    finished_ok = pyqtSignal(list, str)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, params: SearchParams) -> None:
        super().__init__()
        self.params = params

    def run(self) -> None:
        try:
            if self.params.source == "tmdb":
                items = discover_movies(self.params, self.progress.emit)
                self.finished_ok.emit(items, "TMDB")
            else:
                items = local_search(self.params, self.progress.emit)
                self.finished_ok.emit(items, "films.json")
        except AppError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("Se produjo un error inesperado durante la búsqueda.")


class PosterWorker(QThread):
    poster_ready = pyqtSignal(str, str)

    def __init__(self, films: list[dict[str, Any]]) -> None:
        super().__init__()
        self.films = films

    def run(self) -> None:
        for film in self.films:
            if self.isInterruptionRequested():
                break
            try:
                path = ensure_poster(film)
            except Exception:
                path = None
            if path:
                self.poster_ready.emit(str(film.get("id")), str(path))


class FilmsTableWidget(QTableWidget):
    def wheelEvent(self, event: Any) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        scrollbar = self.verticalScrollBar()
        step = -1 if delta > 0 else 1
        scrollbar.setValue(scrollbar.value() + step)
        event.accept()


class SoundtrackSelectionDialog(QDialog):
    def __init__(self, film: dict[str, Any], tracks: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tracks = tracks
        self.selected_track: dict[str, Any] | None = None
        self.setWindowTitle("Seleccionar banda sonora")
        self.resize(760, 420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Pelicula", QLabel(clean_edit_value(film.get("title"))))
        form.addRow("Busqueda", QLabel(f"{clean_edit_value(film.get('original_title') or film.get('title'))} soundtrack"))
        layout.addLayout(form)

        self.list_widget = QListWidget()
        for track in tracks:
            item = QListWidgetItem(self.track_label(track))
            item.setData(Qt.ItemDataRole.UserRole, track)
            self.list_widget.addItem(item)
        self.list_widget.itemClicked.connect(self.play_track)
        layout.addWidget(self.list_widget, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aceptar")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @staticmethod
    def track_label(track: dict[str, Any]) -> str:
        prefix = "[Propuesta] " if track.get("recommended") else ""
        album = clean_edit_value(track.get("album"))
        album_text = f" | {album}" if album else ""
        return (
            f"{prefix}{clean_edit_value(track.get('song_author'))} - "
            f"{clean_edit_value(track.get('song'))}"
            f"{album_text} | popularidad {int(track.get('popularity') or 0)}"
        )

    def play_track(self, item: QListWidgetItem) -> None:
        track = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(track, dict):
            return
        self.selected_track = track
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        uri = spotify_track_uri(track.get("spotify_id"))
        if uri:
            QDesktopServices.openUrl(QUrl(uri))


class RatingValueDialog(QDialog):
    def __init__(
            self,
            film: dict[str, Any],
            field: str,
            current_value: int | None,
            parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.field = field
        self.value_spin = QSpinBox()
        self.value_spin.setRange(1, 5)
        self.value_spin.setValue(current_value or 1)
        self.setWindowTitle("Ajustar valor")

        label = "Dificultad" if field == "difficulty" else "Relevancia"

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Pelicula", QLabel(clean_edit_value(film.get("title"))))
        form.addRow(label, self.value_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aceptar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_value(self) -> int:
        return int(self.value_spin.value())


class FilmsImporter(QWidget):
    def __init__(self) -> None:
        super().__init__()
        ensure_dirs()
        self.genres = load_genres()
        self.results: list[dict[str, Any]] = []
        self.display_results: list[dict[str, Any]] = []
        self.selected_ids: set[str] = set()
        self.current_page = 0
        self.last_operation = "Aplicación iniciada"
        self.current_source_label = "TMDB"
        self.search_worker: SearchWorker | None = None
        self.poster_worker: PosterWorker | None = None
        self.spotify_client: SpotifyClient | None = None
        self.pending_soundtrack_recommendations = parse_pending_soundtrack_recommendations()
        self.sort_column: int | None = None
        self.sort_direction = 0
        self.applying_year_preset = False
        self.dirty_song_ids: set[str] = set()
        self.dirty_rating_ids: set[str] = set()

        self.setWindowTitle("TMDB Films Import Manager")
        self.resize(1450, 850)
        self.build_ui()
        self.ensure_initial_files()
        self.update_genre_list()
        self.update_source_state()
        self.refresh_table()
        self.update_status()

    def build_ui(self) -> None:
        root = QVBoxLayout(self)

        self.status_label = QLabel()
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setStyleSheet("font-weight: 600; padding: 6px;")
        root.addWidget(self.status_label)

        filters = QGroupBox("Filtros")
        filters.setMaximumHeight(190)
        filters_layout = QVBoxLayout(filters)
        filters_layout.setContentsMargins(8, 8, 8, 8)
        filters_layout.setSpacing(6)

        self.tmdb_radio = QRadioButton("TMDB")
        self.local_radio = QRadioButton("films.json")
        self.tmdb_radio.setChecked(True)
        self.source_group = QButtonGroup(self)
        self.source_group.addButton(self.tmdb_radio)
        self.source_group.addButton(self.local_radio)
        self.tmdb_radio.toggled.connect(self.update_source_state)

        top_filters = QHBoxLayout()
        top_filters.setSpacing(10)

        source_box = QGroupBox("Fuente")
        source_layout = QHBoxLayout(source_box)
        source_layout.setContentsMargins(8, 4, 8, 4)
        source_layout.addWidget(self.tmdb_radio)
        source_layout.addWidget(self.local_radio)
        top_filters.addWidget(source_box, 0)

        self.year_preset_combo = QComboBox()
        self.populate_year_presets()
        self.year_preset_combo.currentIndexChanged.connect(self.apply_year_preset)
        self.year_from = QSpinBox()
        self.year_from.setRange(MIN_YEAR, CURRENT_YEAR)
        self.year_from.setValue(MIN_YEAR)
        self.year_to = QSpinBox()
        self.year_to.setRange(MIN_YEAR, CURRENT_YEAR)
        self.year_to.setValue(CURRENT_YEAR)
        self.year_from.valueChanged.connect(self.mark_manual_year_range)
        self.year_to.valueChanged.connect(self.mark_manual_year_range)

        date_box = QGroupBox("Fecha")
        date_layout = QGridLayout(date_box)
        date_layout.setContentsMargins(8, 4, 8, 4)
        date_layout.setHorizontalSpacing(8)
        date_layout.addWidget(QLabel("Rango"), 0, 0)
        date_layout.addWidget(self.year_preset_combo, 0, 1)
        date_layout.addWidget(QLabel("Desde"), 0, 2)
        date_layout.addWidget(self.year_from, 0, 3)
        date_layout.addWidget(QLabel("Hasta"), 0, 4)
        date_layout.addWidget(self.year_to, 0, 5)
        top_filters.addWidget(date_box, 1)

        self.title_filter_edit = QLineEdit()
        self.title_filter_edit.setPlaceholderText("Título en castellano o inglés")
        self.title_filter_edit.returnPressed.connect(self.search)

        title_box = QGroupBox("Texto")
        title_layout = QHBoxLayout(title_box)
        title_layout.setContentsMargins(8, 4, 8, 4)
        title_layout.addWidget(self.title_filter_edit)
        top_filters.addWidget(title_box, 1)

        self.difficulty_filter_combo = QComboBox()
        self.relevance_filter_combo = QComboBox()
        self.populate_rating_filter_combo(self.difficulty_filter_combo)
        self.populate_rating_filter_combo(self.relevance_filter_combo)

        rating_box = QGroupBox("Valoracion")
        rating_layout = QGridLayout(rating_box)
        rating_layout.setContentsMargins(8, 4, 8, 4)
        rating_layout.setHorizontalSpacing(8)
        rating_layout.addWidget(QLabel("Dificultad"), 0, 0)
        rating_layout.addWidget(self.difficulty_filter_combo, 0, 1)
        rating_layout.addWidget(QLabel("Relevancia"), 0, 2)
        rating_layout.addWidget(self.relevance_filter_combo, 0, 3)
        top_filters.addWidget(rating_box, 0)

        self.limit_combo = QComboBox()
        for value in LIMIT_OPTIONS:
            self.limit_combo.addItem(str(value), value)
        self.limit_combo.setCurrentText("500")

        limit_box = QGroupBox("Resultados")
        limit_layout = QHBoxLayout(limit_box)
        limit_layout.setContentsMargins(8, 4, 8, 4)
        limit_layout.addWidget(QLabel("Límite"))
        limit_layout.addWidget(self.limit_combo)
        top_filters.addWidget(limit_box, 0)

        actions_box = QGroupBox("Acciones")
        actions_layout = QHBoxLayout(actions_box)
        actions_layout.setContentsMargins(8, 4, 8, 4)
        self.search_button = QPushButton("Buscar")
        self.reset_button = QPushButton("Resetear filtros")
        self.search_button.clicked.connect(self.search)
        self.reset_button.clicked.connect(self.reset_filters)
        actions_layout.addWidget(self.search_button)
        actions_layout.addWidget(self.reset_button)
        top_filters.addWidget(actions_box, 0)

        filters_layout.addLayout(top_filters)

        genres_box = QGroupBox("Géneros")
        genres_layout = QHBoxLayout(genres_box)
        genres_layout.setContentsMargins(8, 4, 8, 4)
        genres_layout.setSpacing(8)

        self.genre_list = QListWidget()
        self.genre_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.genre_list.setMinimumHeight(70)
        self.genre_list.setMaximumHeight(90)
        self.select_all_genres_button = QPushButton("Seleccionar todos")
        self.clear_genres_button = QPushButton("Deseleccionar todos")
        self.refresh_genres_button = QPushButton("Actualizar géneros")
        self.select_all_genres_button.clicked.connect(lambda: self.set_all_genres(True))
        self.clear_genres_button.clicked.connect(lambda: self.set_all_genres(False))
        self.refresh_genres_button.clicked.connect(self.refresh_genres)
        genres_layout.addWidget(self.genre_list, 1)
        genres_layout.addWidget(self.select_all_genres_button)
        genres_layout.addWidget(self.clear_genres_button)
        genres_layout.addWidget(self.refresh_genres_button)
        filters_layout.addWidget(genres_box)

        root.addWidget(filters)

        actions = QHBoxLayout()
        self.select_all_results_button = QPushButton("Seleccionar todo")
        self.clear_all_results_button = QPushButton("Deseleccionar todo")
        self.add_button = QPushButton("Añadir")
        self.remove_button = QPushButton("Retirar")
        self.create_spotify_playlist_button = QPushButton("Crear lista en Spotify")
        self.save_changes_button = QPushButton("Guardar Cambios")
        self.discard_changes_button = QPushButton("Descartar cambios")
        self.select_all_results_button.clicked.connect(self.select_all_results)
        self.clear_all_results_button.clicked.connect(self.clear_all_results)
        self.add_button.clicked.connect(self.add_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.create_spotify_playlist_button.clicked.connect(self.create_spotify_playlist_from_selected)
        self.save_changes_button.clicked.connect(self.save_current_page_changes)
        self.discard_changes_button.clicked.connect(self.discard_current_page_changes)
        actions.addWidget(self.select_all_results_button)
        actions.addWidget(self.clear_all_results_button)
        actions.addStretch()
        actions.addWidget(self.create_spotify_playlist_button)
        actions.addWidget(self.save_changes_button)
        actions.addWidget(self.discard_changes_button)
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        root.addLayout(actions)

        self.table = FilmsTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setSortingEnabled(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.cellChanged.connect(self.handle_cell_changed)
        self.table.cellClicked.connect(self.handle_cell_clicked)
        self.table.customContextMenuRequested.connect(self.open_soundtrack_dialog_from_context)
        self.table.horizontalHeader().sectionClicked.connect(self.handle_header_clicked)
        header = self.table.horizontalHeader()
        header.setSortIndicatorShown(False)
        header.setSectionResizeMode(COL_SELECT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_RANK, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_POSTER, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_POSTER, POSTER_CELL_SIZE[0])
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_ORIGINAL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_YEAR, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DIFFICULTY, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_RELEVANCE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_GENRES, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_DIRECTOR, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_KNOWN_ACTOR, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_SONG, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_SONG_AUTHOR, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_SPOTIFY_ID, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(COL_SPOTIFY_PLAY, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        paging = QHBoxLayout()
        self.prev_button = QPushButton("Anterior")
        self.next_button = QPushButton("Siguiente")
        self.page_label = QLabel("Página 0 / 0")
        self.prev_button.clicked.connect(self.prev_page)
        self.next_button.clicked.connect(self.next_page)
        paging.addStretch()
        paging.addWidget(self.prev_button)
        paging.addWidget(self.page_label)
        paging.addWidget(self.next_button)
        paging.addStretch()
        root.addLayout(paging)

    def ensure_initial_files(self) -> None:
        if not FILMS_FILE.exists():
            save_films([])
        if not self.genres:
            try:
                self.genres = fetch_genres_from_tmdb()
                self.last_operation = "Géneros generados"
            except AppError as exc:
                self.last_operation = str(exc)

    def populate_year_presets(self) -> None:
        self.year_preset_combo.addItem("Todos", (MIN_YEAR, CURRENT_YEAR))
        self.year_preset_combo.addItem("Manual", None)
        decade_start = MIN_YEAR
        while decade_start <= CURRENT_YEAR:
            decade_end = min(decade_start + 9, CURRENT_YEAR)
            self.year_preset_combo.addItem(f"{decade_start}-{decade_end}", (decade_start, decade_end))
            decade_start += 10

    @staticmethod
    def populate_rating_filter_combo(combo: QComboBox) -> None:
        combo.addItem("Todas", 0)
        for value in range(1, 6):
            combo.addItem(str(value), value)

    def apply_year_preset(self, *_args: Any) -> None:
        preset = self.year_preset_combo.currentData()
        if preset is None:
            return
        start, end = preset
        self.applying_year_preset = True
        try:
            self.year_from.setValue(start)
            self.year_to.setValue(end)
        finally:
            self.applying_year_preset = False

    def mark_manual_year_range(self, *_args: Any) -> None:
        if self.applying_year_preset:
            return
        manual_index = self.year_preset_combo.findText("Manual")
        if manual_index >= 0 and self.year_preset_combo.currentIndex() != manual_index:
            self.year_preset_combo.blockSignals(True)
            self.year_preset_combo.setCurrentIndex(manual_index)
            self.year_preset_combo.blockSignals(False)

    def update_genre_list(self) -> None:
        self.genre_list.clear()
        for genre_id, name in sorted(self.genres.items(), key=lambda item: item[1].lower()):
            item = QListWidgetItem(f"{name} ({genre_id})")
            item.setData(Qt.ItemDataRole.UserRole, int(genre_id))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.genre_list.addItem(item)

    def set_all_genres(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.genre_list.count()):
            self.genre_list.item(row).setCheckState(state)

    def selected_genres(self) -> tuple[int, ...]:
        ids: list[int] = []
        for row in range(self.genre_list.count()):
            item = self.genre_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(sorted(ids))

    def update_source_state(self) -> None:
        is_tmdb = self.tmdb_radio.isChecked()
        self.limit_combo.setEnabled(is_tmdb)
        self.difficulty_filter_combo.setEnabled(not is_tmdb)
        self.relevance_filter_combo.setEnabled(not is_tmdb)
        self.current_source_label = "TMDB" if is_tmdb else "films.json"
        self.update_action_visibility()
        self.update_status()

    def update_action_visibility(self) -> None:
        is_tmdb_listing = self.current_source_label == "TMDB"
        self.add_button.setVisible(is_tmdb_listing)
        self.remove_button.setVisible(not is_tmdb_listing)
        self.save_changes_button.setVisible(not is_tmdb_listing)
        self.discard_changes_button.setVisible(not is_tmdb_listing)
        self.update_change_buttons()
        self.update_spotify_playlist_button()

    def reset_filters(self) -> None:
        self.tmdb_radio.setChecked(True)
        self.year_preset_combo.setCurrentIndex(0)
        self.apply_year_preset()
        self.title_filter_edit.clear()
        self.limit_combo.setCurrentText("500")
        self.difficulty_filter_combo.setCurrentIndex(0)
        self.relevance_filter_combo.setCurrentIndex(0)
        self.set_all_genres(True)
        self.last_operation = "Filtros restablecidos"
        self.update_status()

    def make_params(self) -> SearchParams | None:
        if self.year_from.value() > self.year_to.value():
            self.show_error("El año Desde debe ser menor o igual que Hasta.")
            return None
        source = "tmdb" if self.tmdb_radio.isChecked() else "local"
        return SearchParams(
            source=source,
            year_from=self.year_from.value(),
            year_to=self.year_to.value(),
            genres=self.selected_genres(),
            limit=int(self.limit_combo.currentData()),
            title_filter=self.title_filter_edit.text().strip(),
            difficulty=int(self.difficulty_filter_combo.currentData() or 0),
            relevance=int(self.relevance_filter_combo.currentData() or 0),
        )

    def search(self) -> None:
        params = self.make_params()
        if params is None:
            return
        if self.search_worker and self.search_worker.isRunning():
            return
        self.search_button.setEnabled(False)
        self.last_operation = "Buscando..."
        self.update_status()
        self.search_worker = SearchWorker(params)
        self.search_worker.progress.connect(self.search_progress)
        self.search_worker.finished_ok.connect(self.search_finished)
        self.search_worker.failed.connect(self.search_failed)
        self.search_worker.start()

    def search_progress(self, message: str) -> None:
        self.last_operation = message
        self.update_status()

    def search_finished(self, items: list[dict[str, Any]], source_label: str) -> None:
        self.search_button.setEnabled(True)
        self.results = [dict(item, _rank=index + 1) for index, item in enumerate(items)]
        self.display_results = list(self.results)
        self.selected_ids.clear()
        self.current_page = 0
        self.sort_column = None
        self.sort_direction = 0
        self.dirty_song_ids.clear()
        self.dirty_rating_ids.clear()
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.current_source_label = source_label
        self.last_operation = f"{len(items)} resultados cargados"
        self.update_action_visibility()
        self.refresh_table()
        self.update_status()

    def search_failed(self, message: str) -> None:
        self.search_button.setEnabled(True)
        self.last_operation = message
        self.update_status()
        self.show_error(message)

    def page_count(self) -> int:
        if not self.display_results:
            return 0
        return math.ceil(len(self.display_results) / ROWS_PER_PAGE)

    def page_items(self) -> list[dict[str, Any]]:
        start = self.current_page * ROWS_PER_PAGE
        end = start + ROWS_PER_PAGE
        return self.display_results[start:end]

    def imported_ids(self) -> set[str]:
        return {str(item.get("id")) for item in load_films()}

    def refresh_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        imported = self.imported_ids()
        page_items = self.page_items()
        self.table.setRowCount(len(page_items))

        for row, film in enumerate(page_items):
            film_id = str(film.get("id"))
            is_imported_tmdb_result = self.current_source_label == "TMDB" and film_id in imported
            imported_brush = QBrush(QColor("#e6f4ea"))
            dirty_brush = QBrush(QColor("#fff4ce"))
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            checkbox_item.setCheckState(
                Qt.CheckState.Checked if film_id in self.selected_ids else Qt.CheckState.Unchecked
            )
            checkbox_item.setData(Qt.ItemDataRole.UserRole, film_id)
            if is_imported_tmdb_result:
                checkbox_item.setBackground(imported_brush)
            self.table.setItem(row, COL_SELECT, checkbox_item)

            values = [
                film.get("_rank", ""),
                "",
                film.get("title", ""),
                film.get("original_title", ""),
                film.get("year", ""),
                display_rating_value(film.get("difficulty")),
                display_rating_value(film.get("relevance")),
                self.genre_names(film),
                film.get("director", ""),
                film.get("known_actor", ""),
                film.get("song", ""),
                film.get("song_author", ""),
                film.get("spotify_id", ""),
                "",
                display_float(film.get("score")),
                display_float(film.get("tmdb_vote_average")),
                str(film.get("tmdb_vote_count") or 0),
                display_float(film.get("tmdb_popularity")),
                "Sí" if film_id in imported else "No",
            ]
            for index, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, film_id)
                if index in (
                        COL_RANK,
                        COL_YEAR,
                        COL_DIFFICULTY,
                        COL_RELEVANCE,
                        COL_SCORE,
                        COL_RATING,
                        COL_VOTES,
                        COL_POPULARITY,
                ):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if self.current_source_label == "films.json" and index in EDITABLE_MUSIC_COLUMNS:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if is_imported_tmdb_result:
                    item.setBackground(imported_brush)
                if film_id in self.dirty_song_ids and index in EDITABLE_MUSIC_COLUMNS:
                    item.setBackground(dirty_brush)
                if film_id in self.dirty_rating_ids and index in EDITABLE_RATING_COLUMNS:
                    item.setBackground(dirty_brush)
                self.table.setItem(row, index, item)
            self.load_spotify_cell(row, film)
            self.table.setRowHeight(row, POSTER_CELL_SIZE[1])
            self.load_poster_cell(row, film, is_imported_tmdb_result)

        self.table.blockSignals(False)
        self.update_pagination()
        self.start_poster_loading(page_items)
        self.update_change_buttons()
        self.update_spotify_playlist_button()
        self.populate_spotify_info_for_current_page()

    def load_poster_cell(self, row: int, film: dict[str, Any], shaded: bool) -> None:
        label = QLabel("Sin póster")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(*POSTER_CELL_SIZE)
        label.setProperty("film_id", str(film.get("id")))
        if shaded:
            label.setStyleSheet("background-color: #e6f4ea;")
        path = poster_cache_path(film)
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                label.setPixmap(
                    pixmap.scaled(
                        POSTER_DISPLAY_SIZE[0],
                        POSTER_DISPLAY_SIZE[1],
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                label.setText("")
        self.table.setCellWidget(row, COL_POSTER, label)

    def load_spotify_cell(self, row: int, film: dict[str, Any]) -> None:
        button = QPushButton("Reproducir")
        spotify_id = sanitize_spotify_track_id(film.get("spotify_id"))
        button.setEnabled(valid_spotify_id(spotify_id))
        button.setProperty("spotify_id", spotify_id)
        button.clicked.connect(self.play_spotify_track)
        self.table.setCellWidget(row, COL_SPOTIFY_PLAY, button)

    def spotify(self) -> SpotifyClient:
        if self.spotify_client is None:
            self.spotify_client = SpotifyClient()
        return self.spotify_client

    def film_for_id(self, film_id: str) -> dict[str, Any] | None:
        for film in self.display_results:
            if str(film.get("id")) == film_id:
                return film
        for film in self.results:
            if str(film.get("id")) == film_id:
                return film
        return None

    def soundtrack_candidates_for_film(self, film: dict[str, Any]) -> list[dict[str, Any]]:
        film_id = str(film.get("id"))
        english_title = clean_edit_value(film.get("original_title") or film.get("title"))
        if not english_title:
            raise AppError("La pelicula no tiene titulo para buscar en Spotify.")

        recommended_tracks: list[dict[str, Any]] = []
        for recommendation in self.pending_soundtrack_recommendations.get(film_id, []):
            matches = self.spotify().search_tracks(recommendation, limit=5)
            if not matches:
                continue
            best_match = sorted(matches, key=lambda item: int(item.get("popularity") or 0), reverse=True)[0]
            best_match["recommended"] = True
            recommended_tracks.append(best_match)

        search_query = f"{english_title} soundtrack"
        searched_tracks = self.spotify().search_tracks(search_query)
        searched_tracks = sorted(
            searched_tracks,
            key=lambda item: int(item.get("popularity") or 0),
            reverse=True,
        )

        prioritized = dedupe_spotify_tracks(recommended_tracks + searched_tracks)
        return prioritized[:5]

    def open_soundtrack_dialog_from_context(self, pos: Any) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid() or index.column() != COL_SPOTIFY_ID:
            return
        if self.current_source_label != "films.json":
            self.show_error("La seleccion de banda sonora esta disponible en la vista films.json.")
            return

        row = index.row()
        id_item = self.table.item(row, COL_SELECT)
        if id_item is None:
            return
        film_id = str(id_item.data(Qt.ItemDataRole.UserRole))
        film = self.film_for_id(film_id)
        if film is None:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            tracks = self.soundtrack_candidates_for_film(film)
        except AppError as exc:
            QApplication.restoreOverrideCursor()
            self.show_error(str(exc))
            return
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()

        if not tracks:
            self.show_error("Spotify no devolvio canciones para esa pelicula.")
            return

        dialog = SoundtrackSelectionDialog(film, tracks, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_track is None:
            return
        self.apply_soundtrack_selection(row, film_id, dialog.selected_track)

    def apply_soundtrack_selection(self, row: int, film_id: str, track: dict[str, Any]) -> None:
        song = clean_edit_value(track.get("song"))
        song_author = clean_edit_value(track.get("song_author"))
        spotify_id = sanitize_spotify_track_id(track.get("spotify_id"))
        if not song or not song_author or not valid_spotify_id(spotify_id):
            self.show_error("La cancion seleccionada no tiene datos completos de Spotify.")
            return

        updates = {
            "song": song,
            "song_author": song_author,
            "spotify_id": spotify_id,
        }
        for field, value in updates.items():
            self.update_music_field_for_film(film_id, field, value)
        self.set_table_text(row, COL_SONG, song)
        self.set_table_text(row, COL_SONG_AUTHOR, song_author)
        self.set_table_text(row, COL_SPOTIFY_ID, spotify_id)

        self.dirty_song_ids.add(film_id)
        dirty_brush = QBrush(QColor("#fff4ce"))
        for column in EDITABLE_MUSIC_COLUMNS:
            item = self.table.item(row, column)
            if item is not None:
                item.setBackground(dirty_brush)
        self.update_spotify_button_for_row(row, film_id)
        self.last_operation = f"Banda sonora seleccionada: {song_author} - {song}"
        self.update_change_buttons()
        self.update_spotify_playlist_button()
        self.update_status()

    def populate_spotify_info_for_current_page(self) -> None:
        if self.current_source_label != "films.json":
            return
        dirty_ids = self.current_page_dirty_ids()
        candidates = [
            film
            for film in self.page_items()
            if str(film.get("id")) not in dirty_ids
               and valid_spotify_id(sanitize_spotify_track_id(film.get("spotify_id")))
               and not has_complete_song_info(film)
        ]
        if not candidates:
            return

        films = load_films()
        by_id = {str(film.get("id")): film for film in films}
        updated_ids: set[str] = set()
        for film in candidates:
            film_id = str(film.get("id"))
            persisted = by_id.get(film_id)
            if persisted is None or has_complete_song_info(persisted):
                continue
            spotify_id = sanitize_spotify_track_id(persisted.get("spotify_id") or film.get("spotify_id"))
            if not valid_spotify_id(spotify_id):
                continue
            try:
                track = self.spotify().track_info(spotify_id)
            except AppError as exc:
                QMessageBox.warning(
                    self,
                    "Spotify",
                    f"No se pudo obtener informacion de Spotify para:\n"
                    f"{film.get('title') or film_id} ({film_id})\n\n{exc}",
                )
                continue
            persisted["spotify_id"] = spotify_id
            persisted["song"] = track["song"]
            persisted["song_author"] = track["song_author"]
            updated_ids.add(film_id)

        if not updated_ids:
            return
        try:
            save_films(films)
        except Exception:
            self.show_error("No se pudo escribir films.json.")
            return
        self.apply_spotify_updates_to_current_results(by_id, updated_ids)
        self.last_operation = f"{len(updated_ids)} canciones actualizadas desde Spotify"
        self.update_status()

    def apply_spotify_updates_to_current_results(
            self,
            films_by_id: dict[str, dict[str, Any]],
            updated_ids: set[str],
    ) -> None:
        for collection in (self.results, self.display_results):
            for film in collection:
                film_id = str(film.get("id"))
                persisted = films_by_id.get(film_id)
                if film_id not in updated_ids or persisted is None:
                    continue
                film["song"] = persisted.get("song", "")
                film["song_author"] = persisted.get("song_author", "")
                film["spotify_id"] = persisted.get("spotify_id", "")
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                id_item = self.table.item(row, COL_SELECT)
                if id_item is None:
                    continue
                film_id = str(id_item.data(Qt.ItemDataRole.UserRole))
                persisted = films_by_id.get(film_id)
                if film_id not in updated_ids or persisted is None:
                    continue
                for column, field in (
                        (COL_SONG, "song"),
                        (COL_SONG_AUTHOR, "song_author"),
                        (COL_SPOTIFY_ID, "spotify_id"),
                ):
                    item = self.table.item(row, column)
                    if item is not None:
                        item.setText(clean_edit_value(persisted.get(field)))
                self.update_spotify_button_for_row(row, film_id)
        finally:
            self.table.blockSignals(False)

    def play_spotify_track(self) -> None:
        sender = self.sender()
        spotify_id = sender.property("spotify_id") if sender is not None else None
        uri = spotify_track_uri(spotify_id)
        if not uri:
            self.show_error("El spotify_id no tiene un formato valido.")
            return
        QDesktopServices.openUrl(QUrl(uri))

    def update_spotify_button_for_row(self, row: int, film_id: str) -> None:
        item = self.table.item(row, COL_SPOTIFY_ID)
        if item is None:
            return
        button = self.table.cellWidget(row, COL_SPOTIFY_PLAY)
        if isinstance(button, QPushButton):
            spotify_id = sanitize_spotify_track_id(item.text())
            button.setProperty("spotify_id", spotify_id)
            button.setEnabled(valid_spotify_id(spotify_id))

    def update_music_field_for_film(self, film_id: str, field: str, value: str) -> None:
        for collection in (self.results, self.display_results):
            for film in collection:
                if str(film.get("id")) == film_id:
                    film[field] = value
                    break

    def update_rating_field_for_film(self, film_id: str, field: str, value: int) -> None:
        for collection in (self.results, self.display_results):
            for film in collection:
                if str(film.get("id")) == film_id:
                    film[field] = value
                    break

    def set_table_text(self, row: int, column: int, value: str) -> None:
        item = self.table.item(row, column)
        if item is None:
            return
        self.table.blockSignals(True)
        try:
            item.setText(value)
        finally:
            self.table.blockSignals(False)

    def populate_spotify_info_for_edited_id(self, row: int, film_id: str, spotify_id: str) -> None:
        if not valid_spotify_id(spotify_id):
            return
        try:
            track = self.spotify().track_info(spotify_id)
        except AppError as exc:
            trace_exception(f"No se pudo obtener informacion de Spotify para {spotify_id}", exc)
            self.update_music_field_for_film(film_id, "song", "")
            self.update_music_field_for_film(film_id, "song_author", "")
            self.set_table_text(row, COL_SONG, "")
            self.set_table_text(row, COL_SONG_AUTHOR, "")
            self.last_operation = f"Spotify ID guardado sin datos de cancion: {spotify_id}"
            QMessageBox.warning(
                self,
                "Spotify",
                f"No se pudo obtener informacion de Spotify para el track:\n"
                f"{spotify_id}\n\n{exc}\n\n"
                "Se conserva el Spotify ID y se dejan vacios la cancion y el autor.",
            )
            return
        except Exception as exc:
            trace_exception(f"Error inesperado consultando Spotify para {spotify_id}", exc)
            self.update_music_field_for_film(film_id, "song", "")
            self.update_music_field_for_film(film_id, "song_author", "")
            self.set_table_text(row, COL_SONG, "")
            self.set_table_text(row, COL_SONG_AUTHOR, "")
            self.last_operation = f"Spotify ID guardado sin datos de cancion: {spotify_id}"
            QMessageBox.warning(
                self,
                "Spotify",
                f"No se pudo obtener informacion de Spotify para el track:\n"
                f"{spotify_id}\n\nSe conserva el Spotify ID y se dejan vacios la cancion y el autor.",
            )
            return
        song = track["song"]
        song_author = track["song_author"]
        self.update_music_field_for_film(film_id, "song", song)
        self.update_music_field_for_film(film_id, "song_author", song_author)
        self.set_table_text(row, COL_SONG, song)
        self.set_table_text(row, COL_SONG_AUTHOR, song_author)
        self.last_operation = f"Spotify actualizado: {song_author} - {song}"

    def current_page_dirty_ids(self) -> set[str]:
        page_ids = {str(film.get("id")) for film in self.page_items()}
        return page_ids.intersection(self.dirty_song_ids.union(self.dirty_rating_ids))

    def selected_spotify_ids(self) -> list[str]:
        spotify_ids: list[str] = []
        for film in self.selected_result_films():
            spotify_id = sanitize_spotify_track_id(film.get("spotify_id"))
            if valid_spotify_id(spotify_id):
                spotify_ids.append(spotify_id)
        return list(dict.fromkeys(spotify_ids))

    def update_spotify_playlist_button(self) -> None:
        if hasattr(self, "create_spotify_playlist_button"):
            self.create_spotify_playlist_button.setEnabled(bool(self.selected_spotify_ids()))

    def update_change_buttons(self) -> None:
        enabled = self.current_source_label == "films.json" and bool(self.current_page_dirty_ids())
        if hasattr(self, "save_changes_button"):
            self.save_changes_button.setEnabled(enabled)
        if hasattr(self, "discard_changes_button"):
            self.discard_changes_button.setEnabled(enabled)

    def save_current_page_changes(self) -> None:
        dirty_ids = self.current_page_dirty_ids()
        if not dirty_ids:
            return
        by_result_id = {str(item.get("id")): item for item in self.results}
        films = load_films()
        saved = 0
        for film in films:
            film_id = str(film.get("id"))
            updated = by_result_id.get(film_id)
            if film_id not in dirty_ids or updated is None:
                continue
            film["song"] = clean_edit_value(updated.get("song"))
            film["song_author"] = clean_edit_value(updated.get("song_author"))
            film["spotify_id"] = sanitize_spotify_track_id(updated.get("spotify_id"))
            difficulty = normalized_rating_value(updated.get("difficulty"))
            relevance = normalized_rating_value(updated.get("relevance"))
            if difficulty is not None:
                film["difficulty"] = difficulty
            if relevance is not None:
                film["relevance"] = relevance
            saved += 1
        try:
            save_films(films)
        except Exception:
            self.show_error("No se pudo escribir films.json.")
            return
        self.dirty_song_ids.difference_update(dirty_ids)
        self.dirty_rating_ids.difference_update(dirty_ids)
        self.last_operation = f"{saved} cambios guardados"
        self.refresh_table()
        self.update_status()

    def discard_current_page_changes(self) -> None:
        dirty_ids = self.current_page_dirty_ids()
        if not dirty_ids:
            return
        originals = {str(film.get("id")): film for film in load_films()}
        for collection in (self.results, self.display_results):
            for film in collection:
                film_id = str(film.get("id"))
                original = originals.get(film_id)
                if film_id not in dirty_ids or original is None:
                    continue
                film["song"] = original.get("song", "")
                film["song_author"] = original.get("song_author", "")
                film["spotify_id"] = original.get("spotify_id", "")
                film["difficulty"] = original.get("difficulty")
                film["relevance"] = original.get("relevance")
        self.dirty_song_ids.difference_update(dirty_ids)
        self.dirty_rating_ids.difference_update(dirty_ids)
        self.last_operation = f"{len(dirty_ids)} cambios descartados"
        self.refresh_table()
        self.update_status()

    def start_poster_loading(self, films: list[dict[str, Any]]) -> None:
        if self.poster_worker and self.poster_worker.isRunning():
            return
        missing = [film for film in films if film.get("poster") and not poster_cache_path(film).exists()]
        if not missing:
            return
        self.poster_worker = PosterWorker(missing)
        self.poster_worker.poster_ready.connect(self.poster_ready)
        self.poster_worker.start()

    def poster_ready(self, film_id: str, path: str) -> None:
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, COL_POSTER)
            if widget and widget.property("film_id") == film_id:
                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    widget.setPixmap(
                        pixmap.scaled(
                            POSTER_DISPLAY_SIZE[0],
                            POSTER_DISPLAY_SIZE[1],
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    widget.setText("")

    def genre_names(self, film: dict[str, Any]) -> str:
        names = [self.genres.get(str(g), str(g)) for g in film.get("genres", [])]
        return ", ".join(names)

    def handle_cell_changed(self, row: int, column: int) -> None:
        item = self.table.item(row, column)
        if item is None:
            return
        film_id = str(item.data(Qt.ItemDataRole.UserRole))
        if column == COL_SELECT:
            if item.checkState() == Qt.CheckState.Checked:
                self.selected_ids.add(film_id)
            else:
                self.selected_ids.discard(film_id)
        elif column in EDITABLE_MUSIC_COLUMNS and self.current_source_label == "films.json":
            fields = {
                COL_SONG: "song",
                COL_SONG_AUTHOR: "song_author",
                COL_SPOTIFY_ID: "spotify_id",
            }
            field = fields[column]
            new_value = clean_edit_value(item.text())
            if column == COL_SPOTIFY_ID:
                new_value = sanitize_spotify_track_id(new_value)
                if item.text() != new_value:
                    self.set_table_text(row, column, new_value)
            self.update_music_field_for_film(film_id, field, new_value)
            self.dirty_song_ids.add(film_id)
            dirty_brush = QBrush(QColor("#fff4ce"))
            for dirty_column in EDITABLE_MUSIC_COLUMNS:
                dirty_item = self.table.item(row, dirty_column)
                if dirty_item is not None:
                    dirty_item.setBackground(dirty_brush)
            self.update_spotify_button_for_row(row, film_id)
            if column == COL_SPOTIFY_ID:
                self.populate_spotify_info_for_edited_id(row, film_id, new_value)
        else:
            return
        self.update_change_buttons()
        self.update_spotify_playlist_button()
        self.update_status()

    def handle_cell_clicked(self, row: int, column: int) -> None:
        if column not in EDITABLE_RATING_COLUMNS:
            return
        if self.current_source_label != "films.json":
            return
        item = self.table.item(row, column)
        if item is None:
            return
        film_id = str(item.data(Qt.ItemDataRole.UserRole))
        film = self.film_for_id(film_id)
        if film is None:
            return

        field = "difficulty" if column == COL_DIFFICULTY else "relevance"
        dialog = RatingValueDialog(film, field, normalized_rating_value(film.get(field)), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        value = dialog.selected_value()
        self.update_rating_field_for_film(film_id, field, value)
        self.set_table_text(row, column, str(value))
        self.dirty_rating_ids.add(film_id)
        dirty_brush = QBrush(QColor("#fff4ce"))
        for dirty_column in EDITABLE_RATING_COLUMNS:
            dirty_item = self.table.item(row, dirty_column)
            if dirty_item is not None:
                dirty_item.setBackground(dirty_brush)
        label = "dificultad" if field == "difficulty" else "relevancia"
        self.last_operation = f"{label.capitalize()} actualizada: {value}"
        self.update_change_buttons()
        self.update_status()

    def handle_header_clicked(self, column: int) -> None:
        imported = self.imported_ids()
        sorters: dict[int, Callable[[dict[str, Any]], Any]] = {
            COL_RANK: lambda item: int(item.get("_rank") or 0),
            COL_TITLE: lambda item: normalize_search_text(item.get("title")),
            COL_ORIGINAL: lambda item: normalize_search_text(item.get("original_title")),
            COL_YEAR: lambda item: int(item.get("year") or 0),
            COL_DIFFICULTY: lambda item: normalized_rating_value(item.get("difficulty")) or 0,
            COL_RELEVANCE: lambda item: normalized_rating_value(item.get("relevance")) or 0,
            COL_GENRES: lambda item: normalize_search_text(self.genre_names(item)),
            COL_DIRECTOR: lambda item: normalize_search_text(item.get("director")),
            COL_KNOWN_ACTOR: lambda item: normalize_search_text(item.get("known_actor")),
            COL_SONG: lambda item: normalize_search_text(item.get("song")),
            COL_SONG_AUTHOR: lambda item: normalize_search_text(item.get("song_author")),
            COL_SPOTIFY_ID: lambda item: normalize_search_text(item.get("spotify_id")),
            COL_SCORE: lambda item: float(item.get("score") or 0),
            COL_RATING: lambda item: float(item.get("tmdb_vote_average") or 0),
            COL_VOTES: lambda item: int(item.get("tmdb_vote_count") or 0),
            COL_POPULARITY: lambda item: float(item.get("tmdb_popularity") or 0),
            COL_IMPORTED: lambda item: 1 if str(item.get("id")) in imported else 0,
        }
        sorter = sorters.get(column)
        if sorter is None:
            return

        header = self.table.horizontalHeader()
        if self.sort_column != column:
            self.sort_column = column
            self.sort_direction = 1
        elif self.sort_direction == 1:
            self.sort_direction = -1
        elif self.sort_direction == -1:
            self.sort_column = None
            self.sort_direction = 0
        else:
            self.sort_column = column
            self.sort_direction = 1

        if self.sort_direction == 0:
            self.display_results = list(self.results)
            header.setSortIndicatorShown(False)
        else:
            self.display_results = sorted(
                self.results,
                key=sorter,
                reverse=self.sort_direction == -1,
            )
            sort_order = (
                Qt.SortOrder.AscendingOrder
                if self.sort_direction == 1
                else Qt.SortOrder.DescendingOrder
            )
            header.setSortIndicator(column, sort_order)
            header.setSortIndicatorShown(True)

        self.current_page = 0
        self.refresh_table()
        self.update_status()

    def select_all_results(self) -> None:
        for film in self.page_items():
            self.selected_ids.add(str(film.get("id")))
        self.refresh_table()

    def clear_all_results(self) -> None:
        for film in self.page_items():
            self.selected_ids.discard(str(film.get("id")))
        self.refresh_table()

    def selected_result_films(self) -> list[dict[str, Any]]:
        return [
            film
            for film in self.display_results
            if str(film.get("id")) in self.selected_ids
        ]

    def create_spotify_playlist_from_selected(self) -> None:
        selected = self.selected_result_films()
        if not selected:
            self.show_error("No hay peliculas seleccionadas.")
            return

        spotify_ids = self.selected_spotify_ids()
        if not spotify_ids:
            self.show_error("Las peliculas seleccionadas no tienen canciones con Spotify ID valido.")
            return

        name, accepted = QInputDialog.getText(
            self,
            "Crear lista en Spotify",
            "Nombre de la lista de reproduccion",
            text="Hitlab Films",
        )
        if not accepted:
            return
        playlist_name = clean_edit_value(name)
        if not playlist_name:
            self.show_error("El nombre de la lista no puede estar vacio.")
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            playlist = self.spotify().create_playlist(playlist_name, spotify_ids)
        except AppError as exc:
            QApplication.restoreOverrideCursor()
            self.show_error(str(exc))
            return
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            trace_exception("No se pudo crear la lista de Spotify", exc)
            self.show_error("No se pudo crear la lista en Spotify.")
            return
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()

        count = playlist.get("count", str(len(spotify_ids)))
        playlist_url = clean_edit_value(playlist.get("url"))
        self.last_operation = f"Lista de Spotify creada: {playlist_name} ({count} canciones)"
        self.update_status()
        QMessageBox.information(
            self,
            "Spotify",
            f"Lista creada en Spotify:\n{playlist_name}\n\nCanciones anadidas: {count}",
        )
        if playlist_url:
            QDesktopServices.openUrl(QUrl(playlist_url))

    def add_selected(self) -> None:
        selected = self.selected_result_films()
        if not selected:
            self.show_error("No hay películas seleccionadas.")
            return
        try:
            films = load_films()
            by_id = {str(item.get("id")): item for item in films}
            tmdb_client = TMDBClient()
            added = 0
            duplicates = 0
            for item in selected:
                film_id = str(item.get("id"))
                if film_id in by_id:
                    existing = by_id[film_id]
                    if needs_credits_update(existing):
                        self.last_operation = f"Consultando créditos: {existing.get('title') or film_id}"
                        self.update_status()
                        enrich_movies_with_credits([existing], tmdb_client)
                    duplicates += 1
                    continue
                clean = {
                    "id": film_id,
                    "tmdb_type": "movie",
                    "type": 1,
                    "poster": item.get("poster"),
                    "title": item.get("title") or "",
                    "original_title": item.get("original_title") or "",
                    "year": item.get("year"),
                    "date": item.get("date"),
                    "genres": [int(g) for g in item.get("genres", [])],
                    "director": "",
                    "known_actor": "",
                    "credits_selection_version": 0,
                    "score": item.get("score") or 0,
                    "tmdb_vote_average": item.get("tmdb_vote_average") or 0,
                    "tmdb_vote_count": item.get("tmdb_vote_count") or 0,
                    "tmdb_popularity": item.get("tmdb_popularity") or 0,
                    "song": clean_edit_value(item.get("song")),
                    "song_author": clean_edit_value(item.get("song_author")),
                    "spotify_id": sanitize_spotify_track_id(item.get("spotify_id")),
                }
                self.last_operation = f"Consultando créditos: {clean.get('title') or film_id}"
                self.update_status()
                enrich_movies_with_credits([clean], tmdb_client)
                by_id[film_id] = clean
                added += 1
            save_films(list(by_id.values()))
            self.last_operation = f"{added} añadidas, {duplicates} ya existían"
            self.refresh_table()
            self.update_status()
        except AppError as exc:
            self.show_error(str(exc))
        except Exception:
            self.show_error("No se pudo escribir films.json.")

    def remove_selected(self) -> None:
        selected = self.selected_result_films()
        if not selected:
            self.show_error("No hay películas seleccionadas.")
            return
        selected_ids = {str(item.get("id")) for item in selected}
        existing = load_films()
        remove_count = sum(1 for item in existing if str(item.get("id")) in selected_ids)
        if remove_count == 0:
            self.last_operation = "Ninguna seleccionada estaba importada"
            self.update_status()
            return
        answer = QMessageBox.question(
            self,
            "Confirmar retirada",
            f"¿Desea eliminar {remove_count} películas del catálogo?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            remaining = [item for item in existing if str(item.get("id")) not in selected_ids]
            save_films(remaining)
            self.last_operation = f"{remove_count} eliminadas"
            self.refresh_table()
            self.update_status()
        except Exception:
            self.show_error("No se pudo escribir films.json.")

    def refresh_genres(self) -> None:
        try:
            self.genres = fetch_genres_from_tmdb()
            self.update_genre_list()
            self.last_operation = "Géneros actualizados"
            self.update_status()
        except AppError as exc:
            self.show_error(str(exc))

    def update_pagination(self) -> None:
        pages = self.page_count()
        current = self.current_page + 1 if pages else 0
        self.page_label.setText(f"Página {current} / {pages}")
        self.prev_button.setEnabled(self.current_page > 0)
        self.next_button.setEnabled(self.current_page + 1 < pages)

    def prev_page(self) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_table()
            self.update_status()

    def next_page(self) -> None:
        if self.current_page + 1 < self.page_count():
            self.current_page += 1
            self.refresh_table()
            self.update_status()

    def update_status(self) -> None:
        films_count = len(load_films())
        pages = self.page_count()
        current = self.current_page + 1 if pages else 0
        self.status_label.setText(
            f"films.json: {films_count} películas  |  "
            f"Fuente: {self.current_source_label}  |  "
            f"Página: {current} / {pages}  |  "
            f"Resultados: {len(self.display_results)}  |  "
            f"Seleccionadas: {len(self.selected_ids)}  |  "
            f"Última operación: {self.last_operation}"
        )

    def show_error(self, message: str) -> None:
        self.last_operation = message
        self.update_status()
        QMessageBox.warning(self, "Aviso", message)

    def closeEvent(self, event: Any) -> None:
        for worker in (self.search_worker, self.poster_worker):
            if worker and worker.isRunning():
                worker.requestInterruption()
                worker.wait(500)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = FilmsImporter()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
