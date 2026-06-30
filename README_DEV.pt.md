# DT4MOB — Manual do Desenvolvedor

Digital Twin para Gestão de Autoestradas Resilientes às Alterações Climáticas.
Previsão de propagação de incêndios florestais para os corredores da autoestrada Brisa em Portugal.

---

## Tecnologias

### Linguagens

| Linguagem | Onde | Propósito |
|-----------|------|-----------|
| Python 3.11 | `ingestion/`, `data_preparation/` | Lógica principal da aplicação, pipeline, processamento raster |
| C++ | Motor ForeFire (externo) | Simulação de propagação de fogo (modelo Rothermel) |
| YAML | `docker-compose.yml`, Helm `chart/` | Orquestração de contentores, implantação Kubernetes |
| HTML/JavaScript | Frontend (externo, não no repositório) | Interface de mapa Leaflet |

### Ferramentas Principais

| Ferramenta | Propósito |
|------------|-----------|
| Docker / Podman | Contentorização |
| Docker Compose | Orquestração de desenvolvimento local |
| Helm | Gestão de pacotes Kubernetes |
| Eclipse Ditto | Plataforma de gémeo digital (eventos WebSocket + API HTTP) |
| Keycloak | Autenticação OAuth2/OIDC |
| SeaweedFS / MinIO | Armazenamento de objetos compatível com S3 |
| GDAL | Processamento raster geoespacial |

### Serviços Externos (não no repositório)

- **Eclipse Ditto** — recebe eventos `new_ignition` via WebSocket, armazena things de estações meteorológicas, fornece API de pesquisa espacial
- **Keycloak** — emite tokens JWT para autenticação no Ditto
- **SeaweedFS** (ou MinIO) — armazena ficheiros de saída da simulação (GeoJSON, GLTF)

---

## Pacotes Python

Todas as 16 dependências em `ingestion/requirements.txt`:

| Pacote | Versão | Propósito |
|--------|--------|-----------|
| `requests` | 2.31.0 | Pedidos HTTP (transferências de dados) |
| `numpy` | 1.26.4 | Arrays numéricos, operações em grelhas |
| `netCDF4` | 1.6.5 | I/O de ficheiros NetCDF para `landscape.nc` |
| `rasterio` | 1.3.10 | Leitura, reprojeção e escrita de GeoTIFF |
| `pyproj` | 3.6.1 | Transformações de sistemas de referência de coordenadas |
| `shapely` | 2.0.4 | Operações geométricas (interseção, polígono) |
| `python-dotenv` | 1.0.1 | Carregamento de ficheiro `.env` |
| `GDAL` | 3.6.4 | Processamento raster geoespacial (warp, declive, exposição) |
| `trimesh` | 4.12.2 | Geração de malhas 3D e exportação GLTF |
| `scipy` | 1.17.1 | Triangulação de Delaunay, interpolação IDW |
| `mapbox_earcut` | 2.0.0 | Triangulação de polígonos (usado pelo trimesh) |
| `websockets` | 16.0 | Cliente WebSocket para streaming de eventos Ditto |
| `pydantic` | 2.13.3 | Modelos de validação de dados |
| `pydantic-settings` | 2.14.1 | Gestão de configurações a partir de variáveis de ambiente |
| `httpx` | 0.28.1 | Cliente HTTP (assíncrono + síncrono, usado para API Ditto) |
| `boto3` | 1.43.10 | Cliente de armazenamento compatível com S3 |

---

## Estrutura do Projeto

```
forefire_brisa_edited/
├── README_USER.md                  # Manual do utilizador (implantação e uso)
├── README_DEV.md                   # Este ficheiro
├── .gitignore
│
├── app_deploy/
│   ├── .env.example                # Modelo de variáveis de ambiente
│   ├── docker-compose.yml          # Serviços Docker Compose
│   │
│   ├── ingestion/                  # ★ Aplicação principal
│   │   ├── Dockerfile              # Multi-estágio: compilação ForeFire C++ + runtime Python
│   │   ├── requirements.txt        # Dependências Python
│   │   ├── main.py                 # Ponto de entrada — worker WebSocket assíncrono Ditto
│   │   ├── pipeline.py             # Orquestrador do pipeline de simulação
│   │   ├── wind.py                 # Dados eólicos IPMA, pesquisa espacial Ditto, interpolação IDW
│   │   ├── landscape.py            # Constrói landscape.nc (NetCDF) + fuels.csv
│   │   ├── forefire_runner.py      # Gerador de script .ff + wrapper de subprocesso
│   │   ├── cone_management.py      # Geometria rápida de cone (estágio 1)
│   │   ├── geojson.py              # I/O GeoJSON (zonas Brisa, saves de cone)
│   │   ├── s3_uploader.py          # Carregador de ficheiros S3 (SeaweedFS)
│   │   ├── gltf_experter.py        # Exportação de malha 3D GLTF
│   │   ├── quadkeys.py             # Cálculo de QuadTile para pesquisa espacial Ditto
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── constants.py        # Timesteps da frente de fogo, cores
│   │   │   ├── fire_incident.py    # Modelos Pydantic para things Ditto
│   │   │   └── ditto_body_maker.py # Padrão builder para payloads de things Ditto
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── auth/
│   │   │   │   └── __init__.py     # Autenticação Keycloak OAuth2
│   │   │   └── ditto/
│   │   │       ├── __init__.py     # Singleton DittoClient
│   │   │       ├── ditto_class.py  # Cliente Ditto WebSocket + HTTP
│   │   │       └── utils.py        # Helpers de pesquisa/filtro, cabeçalhos
│   │   └── settings/
│   │       ├── __init__.py         # Classe de configuração (pydantic-settings)
│   │       ├── auth.py             # Configurações de autenticação (endpoint token Keycloak)
│   │       ├── ditto.py            # Configurações de conexão Ditto (URL API, caminho WS)
│   │       └── s3.py              # Configurações S3 (endpoint, bucket, credenciais)
│   │
│   ├── data_preparation/
│   │   ├── Dockerfile              # Imagem Python para preparação raster
│   │   ├── requirements.txt        # Subconjunto de dependências (rasterio, GDAL, boto3)
│   │   └── prepare_rasters.py      # Pipeline raster DEM + COSc → COG
│   │
│   ├── forefire/
│   │   ├── Dockerfile              # Contentor standalone da interface web ForeFire
│   │   └── start.ff                # Script de escuta ForeFire
│   │
│   └── data/
│       ├── dem/                    # Rasters DEM de origem
│       ├── cos/                    # Rasters COSc de origem
│       ├── raw/
│       │   ├── brisa_zones.geojson # Polígonos da autoestrada Brisa
│       │   └── helper_pod.yaml     # Pod utilitário K8s
│       ├── processed/              # Rasters COG de saída (gitignored)
│       └── simulations/            # Ficheiros ForeFire por execução (gitignored)
│
└── chart/                          # Gráfico Helm para Kubernetes
    ├── Chart.yaml
    ├── values.yaml
    └── templates/
        ├── _helpers.tpl
        ├── api-deployment.yaml
        ├── api-service.yaml
        ├── prepare-job.yaml
        ├── pvc.yaml
        ├── secret.yaml
        ├── s3-bucket.yaml
        ├── s3-identity-creds.yaml
        ├── s3-policy-app.yaml
        └── s3-policy-job.yaml
```

---

## Análise Detalhada dos Módulos

### `main.py` — Ponto de Entrada

Um worker assíncrono que se conecta ao Eclipse Ditto via WebSocket e escuta eventos `new_ignition`:

```python
await ditto_client.connect("fire", process_ignition)
```

O callback `process_ignition` é invocado para cada evento. O worker lida com encerramento gracioso através de handlers de sinal `SIGINT`/`SIGTERM`.

### `pipeline.py` — Orquestrador do Pipeline

Duas funções:

- **`process_ignition(message)`** — chamada para cada evento Ditto. Gera o cone rápido, verifica interseção com zonas Brisa via GeoJSON local, envia cone para S3, atualiza o estado do thing Ditto e, condicionalmente, lança `run_pipeline()`.

- **`run_pipeline(ocorrencia_id, lat, lon)`** — executa a simulação ForeFire completa:
  1. Consulta as 5 estações meteorológicas mais próximas a partir da pesquisa espacial Ditto
  2. Computa interpolação IDW do vento numa grelha 50×50
  3. Constrói `landscape.nc` a partir de rasters COG (reprojetados para EPSG:3763)
  4. Escreve `fuels.csv` com parâmetros Rothermel fixos
  5. Gera script ForeFire `.ff`
  6. Executa ForeFire como subprocesso (timeout de 10 minutos)
  7. Analisa o perímetro de saída GeoJSON (8 ficheiros, intervalos de 15 min)
  8. Exporta modelos 3D GLTF (cone + perímetros)
  9. Envia todos os ficheiros para S3
  10. Atualiza o thing Ditto com URLs de resultado e estado final

Máquina de estados: `new_ignition` → `simulating` → `simulated` / `simulation_failed` / `no_risk`

### `wind.py` — Dados de Vento

- **`DIR_MAP`**: Código de direção IPMA (1-9) → graus horários a partir do Norte
- **`_dir_code_to_uv(intensity, code)`**: converte direção IPMA "de onde o vento sopra" para componentes u/v meteorológicas "para onde o vento sopra"
- **`get_nearest_stations(conn, lat, lon, n)`**: consulta pesquisa espacial Ditto usando indexação QuadTile, expandindo o nível de zoom até encontrar `n` estações. Filtra estações com dados de vento válidos (`wind_direction != 9`, `wind_intensity > 0`).
- **`idw_wind_grid(stations, grid_lats, grid_lons, power=2)`**: Interpolação por Inverse Distance Weighting usando distâncias geodésicas (pyproj Geod). Para o caso de estação única, preenche toda a grelha com valor constante.

### `landscape.py` — Construtor NetCDF + CSV

- **`build_landscape_nc()`**: lê rasters COG (`dem.tif`, `fuel_type.tif`) de `/data/processed/`, reprojeta para coordenadas métricas EPSG:3763, recorta para a caixa delimitadora da simulação e escreve NetCDF.
- **`_read_reprojected()`**: extrai uma bbox de um GeoTIFF e reprojeta em tempo real para EPSG:3763 a uma resolução especificada.
- **`write_fuels_csv()`**: escreve uma tabela de parâmetros Rothermel fixa que corresponde aos códigos COSc 2025 (13 tipos de combustível, incluindo o não combustível ID 0).

Os arrays raster são invertidos verticalmente (`np.flipud`) porque o `rasterio` armazena a linha 0 a norte, enquanto a convenção ForeFire/NetCDF espera a linha 0 a sul.

### `forefire_runner.py` — Wrapper ForeFire

- **`build_ff_script()`**: gera um script `.ff` com:
  - Referência à tabela de combustível, modelo de propagação Rothermel
  - Parâmetros de simulação (resolução, redução do vento, velocidade mínima)
  - `loadData` para `landscape.nc`
  - `startFire` com lon/lat de ignição
  - 8 × blocos `goTo[t=900]` + `print[step_NNNN.geojson]` (intervalos de 15 min, 2 h total)
- **`run_forefire(sim_dir, script_path, timeout=600)`**: executa `forefire -i script.ff` como subprocesso com tratamento de erros (timeout, binário não encontrado, erros de SO).
- **`parse_perimeters(sim_dir)`**: lê todos os 8 ficheiros `step_NNNN.geojson`, retorna lista de dicionários `{timestep_min, geojson}`.

### `cone_management.py` — Cone Rápido

- **`generate_cone_from_ignition(lat, lng)`**: obtém estação mais próxima, calcula taxa de propagação (`0.18 × velocidade_vento km/h`), gera 4 segmentos de cone em horizontes de 30/60/90/120 min. O cone abrange ±15° em torno da direção do vento (`wind_direction + 180°`).
- **`cone_intersects_polygons(segments, risk_polygons)`**: verifica interseção Shapely entre qualquer segmento do cone e qualquer polígono de zona Brisa.
- **`_destination_point(lat, lng, bearing, distance_km)`**: fórmula haversine para cálculos de ponto em rumo.

### `geojson.py` — I/O GeoJSON Local

- **`read_risky_areas_from_geojson(file_path)`**: carrega zonas da autoestrada Brisa a partir de um ficheiro GeoJSON local em polígonos Shapely.
- **`save_cone(cones, base_path)`**: escreve o cone gerado como uma FeatureCollection GeoJSON no disco.

### `s3_uploader.py` — Upload S3

- **`upload_simulation_dir_to_seaweed_minio(simulationId, type_send)`**: envia ficheiros para armazenamento compatível com S3 via boto3. Dois modos:
  - `"cone"`: envia `cone_horizon.geojson` e `fire_cone.glb`
  - `"perimeters"`: envia ficheiros `step_*.geojson` e `fire_simulation.glb`
- Retorna um dicionário mapeando nomes de ficheiro para os seus URLs S3.
- Usa endereçamento path-style, assinaturas s3v4 e configuração de transferência multipart.

### `gltf_experter.py` — Exportação 3D GLTF

- **`export_perimeters_geojson_to_gltf()`**: lê perímetros de GeoJSON, amostra elevação de `landscape.nc` via `RectBivariateSpline`, triangula com Delaunay, constrói uma `trimesh.Scene` com frentes de fogo codificadas por cor (rampa verde→vermelho).
- **`export_cone_geojson_to_gltf()`**: mesma abordagem para a geometria do cone.
- Cada segmento de frente/cone recebe uma cor única de `FIREFRONT_COLORS`.
- Um ponto de ignição (esfera azul) é colocado na origem.
- Saída: `fire_simulation.glb` e `fire_cone.glb`.

### `quadkeys.py` — Indexação QuadTile

Implementa o sistema QuadTile usado pelo Eclipse Ditto para indexação espacial. Funções:

- **`get_quadkey(lat, lng, zoom)`**: codifica lat/lon num quadkey inteiro de 64 bits (zoom máximo 31).
- **`get_tile_bounds(lat, lng, tile_zoom, max_zoom=31)`**: calcula limites inferior e superior do quadkey para um tile no nível de zoom dado, usado em consultas de filtro Ditto.

### `models/fire_incident.py` — Modelos Pydantic

- `Point` — par lat/lon
- `fireState` — enum: `NEW_IGNITION`, `NO_RISK`, `SIMULATING`, `SIMULATED`, `FAILED`
- `ConeSection` — pontos do polígono + minutos do horizonte
- `fireIncidentThing` — estrutura do thing Ditto com `thingId`, `policyId`, `attributes` (ponto de ignição, estado, URLs de polígono, expiração) e `features` (URL do perímetro do cone, lista de perímetros)

### `models/ditto_body_maker.py` — Padrão Builder

`DittoBodyBuilder` implementa uma interface fluente de builder para construir payloads de things Ditto:

```python
thing = (DittoBodyBuilder(policy_id="fire:default", thing_id=thing_id)
         .ignition(lat, lon)
         .cones(cone_url)
         .fire_state(fireState.SIMULATING)
         .polygon(polygon_urls)
         .perimeters(perimeter_urls)
         .build())
```

### `services/ditto/ditto_class.py` — Cliente Ditto

`DittoClient` gere:
- **Conexão WebSocket** ao Ditto para streaming de eventos
- **Loop de refresh de token** — refresca periodicamente o JWT Keycloak e envia-o ao Ditto via mensagem de controlo `JWT-TOKEN`
- **Loop de escuta de eventos** — recebe mensagens WebSocket, despacha para o callback `process_ignition()`
- **Métodos HTTP**:
  - `update_fire_incident()` — pedido PATCH para atualizar estado do thing
  - `get_stations_zoom_sync()` — pedido GET para search/things com filtro QuadTile

Mensagens de controlo:
- `START-SEND-EVENTS?namespaces=fire&filter=eq(attributes/state,'new_ignition')` — subscreve eventos de ignição
- `JWT-TOKEN?jwtToken=...` — refresca autorização WebSocket

### `services/ditto/utils.py` — Helpers Ditto

- `prepare_search_params()`: constrói strings de filtro Ditto usando limites QuadTile para pesquisar things de estações meteorológicas numa área geográfica.
- `get_headers()`: retorna cabeçalhos Content-Type apropriados (`application/merge-patch+json` para atualizações, `application/json` para criações).

### `services/auth/__init__.py` — Autenticação Keycloak

`AuthenticationService` gere o fluxo de concessão de password OAuth2:
- Solicita tokens JWT ao Keycloak
- Armazena tokens em cache e refresca a metade do tempo de expiração
- Suporta credenciais baseadas em ficheiros para segredos Kubernetes (via `AUTH_USERNAME_FILE` / `AUTH_PASSWORD_FILE`)
- Suporta certificados CA personalizados para conexões TLS

### `settings/` — Configuração

Usa `pydantic-settings` com `env_nested_delimiter="__"` para variáveis de ambiente aninhadas:

| Variável env | Configuração | Predefinição |
|--------------|--------------|--------------|
| `LOG_LEVEL` | `log_level` | `DEBUG` |
| `RISKY_AREAS_PATH` | `risk_areas_dir` | `/data/raw/brisa_zones.geojson` |
| `FOREFIRE_BIN` | `forefire_bin` | `/usr/local/bin/forefire` |
| `SIM_BASE_DIR` | `simulations_dir` | `/data/simulations` |
| `DITTO__API_URL` | `ditto.api_url` | `https://localhost:8080` |
| `DITTO__BASE_API_PATH` | `ditto.base_api_path` | `/api/2` |
| `DITTO__WS__PATH` | `ditto.base_ws_path` | `/ws/2` |
| `AUTH__TOKEN_ENDPOINT` | `auth.token_endpoint` | `https://localhost:8080/auth/realms/dt4mob/protocol/openid-connect/token` |
| `AUTH__CLIENT_ID` | `auth.client_id` | `ditto` |
| `AUTH__USERNAME` | `auth.username` | — |
| `AUTH__PASSWORD` | `auth.password` | — |
| `AUTH__USERNAME_FILE` | `auth.username_file` | — |
| `AUTH__PASSWORD_FILE` | `auth.password_file` | — |
| `S3__URL_INTERNAL` | `s3.url_internal` | `http://localhost:8333/` |
| `S3__URL_EXTERNAL` | `s3.url_external` | `http://localhost:8333/` |
| `S3__BUCKET` | `s3.bucket` | `test-bucket` |
| `S3__ACCESS_KEY` | `s3.access_key` | — |
| `S3__SECRET_KEY` | `s3.secret_key` | — |
| `COS_URL` | — (usado em `prepare_rasters.py`) | `https://geo2.dgterritorio.gov.pt/cosc/COSc2025.zip` |
| `DEM_URL` | — (usado em `prepare_rasters.py`) | `http://gis.ciimar.up.pt/gis-data/lidar-dgt/MDT-10m-PT.tif` |

### `data_preparation/prepare_rasters.py` — Pré-processamento Raster

Script único que:
1. Transfere DEM de URL (opcional) ou usa ficheiro em `data/dem/`
2. Transfere COSc 2025 de URL (opcional) ou usa ficheiro em `data/cos/`
3. Reprojeta DEM para EPSG:4326 via `gdalwarp`
4. Gera `slope.tif` e `aspect.tif` via `gdaldem`
5. Reprojeta raster COSc para a grelha DEM com reamostragem de vizinho mais próximo
6. Remapeia códigos COSc não combustíveis (100, 500, 620) para 0
7. Escreve todas as saídas como GeoTIFFs Cloud-Optimized em `data/processed/`
8. Cria ficheiro marcador `.done`

No contexto K8s, também transfere o GeoJSON das zonas Brisa do S3 se configurado.

---

## Fluxo de Trabalho em Duas Etapas

### Etapa 1 — Cone Rápido

Acionado por um evento Ditto `new_ignition`:

1. A estação meteorológica mais próxima é obtida da pesquisa espacial Ditto (baseada em QuadTile)
2. Direção/velocidade do vento determinam a geometria do cone:
   - Rumo central = direção do vento + 180° (sentido do vento)
   - Taxa de propagação = `0.18 × velocidade_vento` km/h
   - 4 segmentos de cone a 30/60/90/120 min, ±15° de abertura
3. Cone é guardado no disco local como GeoJSON
4. Modelo 3D GLTF do cone é gerado
5. Cone + GLTF são enviados para S3
6. Thing Ditto é atualizado com `state: simulating` + URLs do cone
7. Zonas Brisa são carregadas do GeoJSON local
8. `shapely.intersects()` verifica interseção cone-vs-zona

### Etapa 2 — Simulação ForeFire Completa

Acionado apenas se o cone intersectar uma zona Brisa:

1. Thing Ditto atualizado para `state: simulating`
2. 5 estações mais próximas obtidas, grelha de vento IDW computada
3. `landscape.nc` construído a partir de rasters COG (altitude a 100 m, combustível a 10 m de resolução)
4. `fuels.csv` escrito (13 tipos de combustível, códigos COSc como índices)
5. Script `.ff` gerado com 8 etapas × 15 min = 2 h total
6. Binário `forefire` executado como subprocesso
7. 8 ficheiros GeoJSON de perímetro analisados
8. Modelo 3D GLTF gerado a partir dos perímetros
9. Todos os ficheiros enviados para S3 (cone + perímetros)
10. Thing Ditto atualizado com `state: simulated` + todos os URLs

---

## Processamento de Vento

### Convenção de Direção IPMA

O `idDireccVento` da IPMA é a direção **de onde** o vento sopra (convenção meteorológica). O sistema converte para componentes u/v:

```python
DIR_MAP = {1: 0, 2: 45, 3: 90, 4: 135, 5: 180, 6: 225, 7: 270, 8: 315, 9: 0}
# código 1 = Norte (de Norte), código 7 = Oeste (de Oeste)

u = -speed * sin(radians(direction))  # para leste (negado = para)
v = -speed * cos(radians(direction))  # para norte (negado = para)
```

Exemplo: código 7 (de Oeste, 270°) → vento a soprar para leste → `u = +speed`, `v ≈ 0`.

### Interpolação IDW

A Inverse Distance Weighting é computada usando distâncias geodésicas (elipsoide WGS84 via pyproj `Geod`):

```python
dist_m = _geod.inv(lon1, lat1, lon2, lat2)  # retorna metros
weight = 1.0 / dist_m**power  # power=2
```

Os valores u/v interpolados em cada célula da grelha são a média ponderada de todos os valores das estações, normalizada pela soma dos pesos.

---

## Formato NetCDF (`landscape.nc`)

O ForeFire usa um **sistema de coordenadas métrico local** (canto SO = origem). A referência geográfica é através de `BBoxWSEN`.

### Dimensões

| Dimensão | Tamanho | Descrição |
|----------|---------|-----------|
| `ny`, `nx` | variável | Grelha de altitude (resolução 100 m) |
| `fy`, `fx` | variável | Grelha de combustível (resolução 10 m) |
| `wind_rows`, `wind_columns` | 50×50 | Resolução da grelha de vento IDW |
| `nt`, `nz`, `ft`, `fz` | 1 | Tempo e nível z (singleton) |

### Variáveis

| Variável | Forma | Tipo | Descrição |
|----------|-------|------|-----------|
| `domain` | escalar | str | Metadados com atributos de referência espacial |
| `altitude` | (1, 1, ny, nx) | int16 | Elevação em metros, linha 0 = sul |
| `fuel` | (1, 1, fy, fx) | int16 | ID do tipo de combustível (0 = não combustível), linha 0 = sul |
| `wind` | (2, 2, wr, wc) | float32 | [u/v, timestep, linhas, colunas]; u = para leste m/s |
| `lat`, `lon` | (ny,), (nx,) | float64 | Coordenadas de latitude/longitude para grelha de altitude |
| `fuel_lat`, `fuel_lon` | (fy,), (fx,) | float64 | Latitude/longitude para grelha de combustível |

### Atributos do Domínio

| Atributo | Valor |
|----------|-------|
| `SWx`, `SWy` | 0.0 (origem local no canto SO) |
| `Lx`, `Ly` | Largura/altura do domínio em metros (EPSG:3763) |
| `BBoxWSEN` | `"xmin,ymin,xmax,ymax"` em graus WGS84 |
| `t0` | 0.0 |
| `Lt` | inf |
| `WSENLBRT` | String combinada de limites WGS84 + locais |

### Convenções de Coordenadas

- Linha 0 = sul (convenção NetCDF/ForeFire). O `rasterio` lê norte-na-linha-0, por isso `np.flipud()` é aplicado.
- O vento é constante em ambos os timesteps (mesmo u/v para t=0 e t=1).
- O ForeFire deriva declive e exposição de `altitude` internamente.

---

## Integração ForeFire

### Formato do Script

```
# Simulação ForeFire — DT4MOB
setParameter[fuelsTableFile=/data/simulations/{id}/fuels.csv]
setParameter[propagationModel=Rothermel]
setParameter[minimalPropagativeFrontDepth=20]
setParameter[perimeterResolution=10]
setParameter[spatialIncrement=3]
setParameter[relax=0.5]
setParameter[windReductionFactor=0.4]
setParameter[minSpeed=0.009]
setParameter[dumpMode=geojson]
setParameter[ForeFireDataDirectory=/data/simulations/{id}]
loadData[/data/simulations/{id}/landscape.nc;2024-01-01T12:00:00Z]
startFire[lonlat=(-8.324000,41.209000,0.);t=0]
goTo[t=900]
print[step_0015.geojson]
goTo[t=1800]
print[step_0030.geojson]
...
goTo[t=7200]
print[step_0120.geojson]
```

8 etapas × 15 min = horizonte de 2 horas. O repositório ForeFire está em https://github.com/forefireAPI/forefire (GPL-3.0).

### Parâmetros de Simulação

| Parâmetro | Valor | Propósito |
|-----------|-------|-----------|
| `propagationModel` | `Rothermel` | Modelo ROS Rothermel |
| `minimalPropagativeFrontDepth` | 20 | Profundidade mínima da frente de fogo (m) |
| `perimeterResolution` | 10 | Espaçamento de vértices do perímetro (m) |
| `windReductionFactor` | 0.4 | Fator de abrigo do vento |
| `minSpeed` | 0.009 | Taxa de propagação mínima (m/s) |

### Mapeamento do Modelo de Combustível (COSc 2025 → Rothermel)

Os IDs de combustível em `fuels.csv` correspondem diretamente aos valores de pixel COSc (não renumerados):

| ID | Código COSc | Nome | Comportamento do Fogo | Carga (t/ha) | SAV (1/m) |
|----|-------------|------|------------------------|--------------|-----------|
| 0 | 100, 500, 620 | Não combustível | Parar | 0.0 | 0 |
| 211 | 211 | Anuais Seco | Combustível fino rápido | 1.8 | 3500 |
| 212 | 212 | Anuais Verde | Lento/verde | 1.2 | 3000 |
| 213 | 213 | Outras Agrícolas | Pastagem mista | 2.5 | 2800 |
| 311 | 311 | Sobreiro/Azinheira | Baixa intensidade | 5.5 | 1800 |
| 312 | 312 | **Eucalipto** | **Alta/Spotting** | **18.5** | **5000** |
| 313 | 313 | Outras Folhosas | Folhada de folhosas | 6.0 | 2200 |
| 321 | 321 | Pinheiro Bravo | Folhada de coníferas | 8.5 | 1500 |
| 322 | 322 | Pinheiro Manso | Sub-bosque limpo | 5.0 | 1400 |
| 323 | 323 | Outras Resinosas | Conífera densa | 7.5 | 1600 |
| 410 | 410 | Matos | Arbustos de alta intensidade | 13.0 | 2000 |
| 420 | 420 | Herbácea Espontânea | Erva rápida | 2.0 | 3500 |
| 610 | 610 | Zonas Húmidas | Paul úmido, lento | 1.5 | 2500 |

O ID 0 é crítico — o ForeFire trata `fuel=0` como uma paragem imediata de propagação (cortas-fogo urbanos, rios, rocha nua).

---

## Contentorização

### Dockerfile Multi-Estágio (`ingestion/Dockerfile`)

**Estágio 1 — forefire-builder (Ubuntu 22.04)**
- Clona `forefireAPI/forefire` do GitHub
- Compila com cmake
- Instala binário em `/forefire/install/bin/`

**Estágio 2 — Runtime Python (python:3.11-slim)**
- Dependências de sistema: GDAL, bibliotecas NetCDF, ferramentas de compilação
- Copia binário ForeFire do estágio de compilação
- Instala dependências Python
- Entrada: `python main.py`

### Docker Compose (`docker-compose.yml`)

Atualmente, apenas o serviço `ingestion` está ativo:
- Usa `network_mode: host` para acesso direto ao Ditto
- Monta `./data:/data:z` (volume partilhado com contexto SELinux)
- Lê credenciais do ficheiro `.env`

Serviços adicionais (comentados): `prepare_rasters_job`, interface standalone `forefire`.

### Gráfico Helm (`chart/`)

| Template | Kind | Propósito |
|----------|------|-----------|
| `api-deployment.yaml` | Deployment | Serviço de ingestão; contentor init aguarda rasters |
| `api-service.yaml` | Service | Expõe porta 8080 |
| `prepare-job.yaml` | Job | Executa `prepare_rasters.py` uma vez |
| `pvc.yaml` | PersistentVolumeClaim | 20Gi para dados de raster + simulação |
| `secret.yaml` | Secret | Credenciais Ditto |
| `s3-identity-creds.yaml` | S3Identity + S3Credentials | Utilizador IAM SeaweedFS |
| `s3-policy-app.yaml` | S3Policy + S3PolicyBinding | Acesso ao bucket para ingestão |
| `s3-policy-job.yaml` | S3Policy + S3PolicyBinding | Acesso ao bucket para job de raster |

Funcionalidades principais do Kubernetes:
- Contentor init aguarda ficheiro `/data/processed/.done` (marcador de preparação raster)
- Credenciais de Secrets Kubernetes montadas em `/run/secrets/dt4mob/`
- Certificados TLS de Secrets Kubernetes para HTTPS do Keycloak
- Limites de recursos: 3Gi pedido / 8Gi limite para job raster; PVC 20Gi

---

## Licença

Todo o código personalizado: a definir pela Brisa / equipa do projeto.
Motor ForeFire: GPL-3.0 (https://github.com/forefireAPI/forefire).
