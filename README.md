# hitlab-registry

Repositorio de catálogos y herramientas de apoyo para el juego HitLab.

El objetivo del repositorio es mantener contenido descargable y versionado que pueda ser consumido por el juego: catálogos temáticos, metadatos de categorías, modos de juego compatibles y utilidades para crear o mantener esos catálogos.

## Contenido

```text
.
|-- metadata.json
|-- category/
|   |-- movies/
|   |   |-- metadata.json
|   |   `-- catalog.json
|   `-- movies-kids/
|       |-- metadata.json
|       `-- catalog.json
`-- tools/
    |-- movies-catalog-manager.py
    `-- target/
        `-- movies-catalog-manager/
```

## Catálogos

Los catálogos están organizados por categoría dentro de `category/`.

Cada categoría contiene:

- `metadata.json`: información del catálogo, como identificador, nombre, descripción, tipo de catálogo y modos de juego compatibles.
- `catalog.json`: datos consumibles por HitLab. En los catálogos de películas incluye metadatos, géneros y entradas del catálogo.

Actualmente el repositorio contiene:

- `category/movies`: catálogo general de películas.
- `category/movies-kids`: catálogo de películas orientado a contenido infantil o familiar.

Las entradas de los catálogos de películas incluyen información como identificador de TMDB, tipo de medio, título, año, fecha, géneros, cartel, puntuaciones, dificultad, relevancia, canción asociada, autor, identificador de Spotify, dirección y actores conocidos.

## Metadatos globales

El archivo `metadata.json` en la raíz define información común del registro, incluyendo:

- Modos de juego soportados, como `turn-based` y `competitive`.
- Tipos de catálogo disponibles.

## Herramientas

### movies-catalog-manager

`tools/movies-catalog-manager.py` es una aplicación de escritorio en Python para crear y mantener el catálogo de películas de HitLab.

La herramienta permite:

- Buscar y descubrir películas y series usando TMDB.
- Mantener una base de películas seleccionadas.
- Gestionar metadatos relevantes para el juego, como dificultad y relevancia.
- Asociar canciones y bandas sonoras mediante Spotify.
- Generar datos JSON para catálogos.
- Cachear datos de películas, géneros y carteles en `tools/target/movies-catalog-manager/`.

#### Requisitos

La herramienta usa Python 3 y depende de:

- `PyQt6`
- `requests`
- `Pillow`

Instalación orientativa:

```bash
python -m pip install PyQt6 requests Pillow
```

#### Configuración

Antes de usar integraciones externas, configura las credenciales necesarias en `tools/movies-catalog-manager.py`:

- `TMDB_BEARER_TOKEN`
- `TMDB_API_KEY`
- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`

La URL de redirección de Spotify puede configurarse con la variable de entorno `SPOTIFY_REDIRECT_URI`. Si no se define, la herramienta usa `http://127.0.0.1:8888/callback`.

#### Ejecución

```bash
python tools/movies-catalog-manager.py
```

Los datos de trabajo de la herramienta se guardan bajo:

```text
tools/target/movies-catalog-manager/
```

Ese directorio contiene archivos como `movies.json`, `genres.json` y cachés locales de películas y carteles.

## Flujo de mantenimiento

1. Editar o generar contenido con las herramientas disponibles.
2. Revisar los datos generados en `tools/target/movies-catalog-manager/`.
3. Actualizar los catálogos publicados bajo `category/<catalogo>/`.
4. Validar que `metadata.json` y `catalog.json` mantienen el formato esperado por HitLab.

## Formato general de un catálogo

Un `catalog.json` contiene:

- `metadata`: versión u otros datos internos del catálogo.
- `genres`: mapa de identificadores de género a nombre legible.
- `catalog`: lista de elementos jugables.

Ejemplo simplificado:

```json
{
  "metadata": {
    "version": "0.1"
  },
  "genres": {
    "28": "Acción"
  },
  "catalog": [
    {
      "id": "123",
      "tmdb_type": "movie",
      "title": "Título",
      "year": 2024,
      "song": "Canción asociada",
      "spotify_id": "..."
    }
  ]
}
```
