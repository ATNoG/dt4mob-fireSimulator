# DT4MOB — Manual do Utilizador

Digital Twin para Gestão de Autoestradas Resilientes às Alterações Climáticas.
Previsão de propagação de incêndios florestais para os corredores da autoestrada Brisa em Portugal.

## Visão Geral

O sistema integra dados de vento em tempo real com o motor de propagação de incêndios ForeFire para:

1. Aceitar pontos de ignição (a partir de uma interface de mapa ou eventos Ditto)
2. Calcular um cone de propagação rápido baseado no vento da estação meteorológica mais próxima
3. Verificar se o cone intersecta uma zona da autoestrada Brisa
4. Se sim, executar uma simulação ForeFire completa produzindo 8 perímetros (intervalos de 15 min, horizonte de 2 horas)
5. Enviar resultados (GeoJSON + modelos 3D GLTF) para armazenamento compatível com S3
6. Atualizar o estado do gémeo digital no Eclipse Ditto

## Arquitetura

```
Estações Meteorológicas IPMA (Ditto Things)    Interface Mapa / Sistema Externo
               │                                      │
               ▼                                      ▼
         ┌─────────────────────────────────────────────────┐
         │          SERVIÇO DE INGESTÃO (Python)            │
         │  • Escuta eventos WebSocket Ditto                │
         │  • Pesquisa estações próximas via API Ditto      │
         │  • Interpolação de vento IDW                     │
         │  • Constrói landscape.nc + fuels.csv             │
         │  • Executa subprocesso ForeFire                  │
         │  • Envia resultados para S3 + atualiza Ditto     │
         └────────────┬────────────────────┬────────────────┘
                      │ subprocesso        │ HTTP / S3
                      ▼                    ▼
         ┌────────────────────┐  ┌────────────────────┐
         │  MOTOR FOREFIRE    │  │  S3 (SeaweedFS)    │
         │  (C++ / Rothermel) │  │  • Ficheiros       │
         │  • landscape.nc    │  │    GeoJSON         │
         │  • step_*.geojson  │  │  • Modelos 3D      │
         │                    │  │    GLTF            │
         │                    │  │  • Dados do cone   │
         └────────────────────┘  └────────────────────┘
                      │
                      ▼
         ┌────────────────────┐
         │  ECLIPSE DITTO     │
         │  • Incêndios       │
         │  • Estações        │
         │    meteorológicas  │
         │  • Pesquisa        │
         │    espacial        │
         └────────────────────┘
```

**Serviços principais dos quais o sistema depende (não incluídos neste repositório):**
- **Eclipse Ditto** — plataforma de gémeo digital (eventos WebSocket, API HTTP)
- **Keycloak** — autenticação OAuth2/OIDC
- **SeaweedFS** (ou qualquer armazenamento compatível com S3, e.g. MinIO) — armazenamento de ficheiros

---

## Pré-requisitos

- Docker Compose v2 (`docker compose`) ou Podman + podman-compose
- Os seguintes ficheiros de dados devem estar disponíveis:

| Ficheiro | Localização | Descrição |
|----------|-------------|-----------|
| DEM | `app_deploy/data/dem/dem.tif` | Modelo digital de elevação LiDAR DGT (qualquer CRS, auto-reprojetado) |
| COSc ocupação do solo | `app_deploy/data/cos/` | Raster COSc 2025 (.tif), transferido automaticamente se URLs configurados |
| Zonas Brisa | `app_deploy/data/raw/brisa_zones.geojson` | Polígonos de concessão da autoestrada Brisa, EPSG:4326 |

**Utilizadores Podman:** alias `docker` e `docker compose` para os equivalentes Podman:
```bash
alias docker=podman
alias docker compose='podman compose'
```
Adicione a `~/.bashrc` ou `~/.zshrc` para tornar permanente.

---

## Configuração

Copie o ficheiro de ambiente de exemplo:

```bash
cp app_deploy/.env.example app_deploy/.env
```

Edite `.env` com as suas credenciais:

| Variável | Predefinição | Descrição |
|----------|--------------|-----------|
| `LOG_LEVEL` | `DEBUG` | Nível de log |
| `RISKY_AREAS_PATH` | `/data/raw/brisa_zones.geojson` | Caminho para o ficheiro GeoJSON das zonas Brisa |
| `FOREFIRE_BIN` | `/usr/local/bin/forefire` | Caminho do binário ForeFire (construído no contentor) |
| `SIM_BASE_DIR` | `/data/simulations` | Diretório de trabalho da simulação |
| `DITTO__API_URL` | `https://localhost:8080` | URL da API Ditto |
| `DITTO__BASE_API_PATH` | `/api/2` | Caminho base da API Ditto |
| `DITTO__WS__PATH` | `/ws/2` | Caminho base do WebSocket Ditto |
| `AUTH__TOKEN_ENDPOINT` | `https://localhost:8080/auth/realms/dt4mob/protocol/openid-connect/token` | Endpoint de token Keycloak |
| `AUTH__CLIENT_ID` | `ditto` | ID de cliente Keycloak |
| `AUTH__USERNAME` | — | Nome de utilizador Ditto |
| `AUTH__PASSWORD` | — | Palavra-passe Ditto |
| `S3__URL_INTERNAL` | `http://localhost:8333/` | URL interno do S3 |
| `S3__URL_EXTERNAL` | `http://localhost:8333/` | URL externo do S3 |
| `S3__BUCKET` | `test-bucket` | Nome do bucket S3 |
| `S3__ACCESS_KEY` | — | Chave de acesso S3 |
| `S3__SECRET_KEY` | — | Chave secreta S3 |
| `COS_URL` | `https://geo2.dgterritorio.gov.pt/cosc/COSc2025.zip` | URL de transferência COSc 2025 |
| `DEM_URL` | `http://gis.ciimar.up.pt/gis-data/lidar-dgt/MDT-10m-PT.tif` | URL de transferência DEM |

---

## Implantação com Docker Compose

### 1. Compilar e iniciar

```bash
cd app_deploy
docker compose up -d --build
```

Isto compila todas as imagens e inicia o serviço de ingestão.

### 2. Preparar rasters de terreno

```bash
docker compose exec ingestion python prepare_rasters.py
```

Transfere DEM e COSc (se URLs configurados), reprojeta para EPSG:4326, produz GeoTIFFs Cloud-Optimized em `data/processed/`:
- `dem.tif` — elevação
- `fuel_type.tif` — códigos de combustível COSc (não combustível remapeado para 0)

### 3. Verificar o serviço

```bash
curl -s http://localhost:8000/api/health          # {"status": "ok"}
curl -s http://localhost:8000/api/wind | python3 -m json.tool  # observações de vento
```

### 4. Aceder a endpoints

| Serviço | URL | Descrição |
|---------|-----|-----------|
| API de Ingestão | `http://localhost:8000` | Aplicação FastAPI (Swagger em `/docs`) |
| Interface Web ForeFire | `http://localhost:8001` | Interface integrada ForeFire (se ativada) |
| Mapa Frontend | `http://localhost:8080` | Mapa Leaflet (se ativado) |

---

## Implantação com Helm (Kubernetes)

O repositório inclui um gráfico Helm em `chart/` para implantação em Kubernetes.

### Pré-requisitos

- Cluster Kubernetes (v1.19+)
- **Operador SeaweedFS** instalado com um cluster SeaweedFS em execução
- **Eclipse Ditto** implantado e acessível
- **Keycloak** implantado e acessível
- Provisionador de PersistentVolume (e.g. `local-path`, `longhorn` ou `ebs`)

### Estrutura do gráfico

```
chart/
├── Chart.yaml                # dt4mob-fire-simulator v0.1.0
├── values.yaml               # Todos os parâmetros configuráveis
└── templates/
    ├── api-deployment.yaml   # Deployment do serviço de ingestão
    ├── api-service.yaml      # Service expondo porta 8080
    ├── prepare-job.yaml      # Job de preparação raster único
    ├── pvc.yaml              # PersistentVolumeClaim de 20Gi
    ├── secret.yaml           # Secret de credenciais Ditto
    ├── s3-identity-creds.yaml# S3Identity + S3Credentials SeaweedFS
    ├── s3-policy-app.yaml    # Política S3 + binding para ingestão
    ├── s3-policy-job.yaml    # Política S3 + binding para job raster
    └── _helpers.tpl          # Helpers de template
```

### Configurações principais do values.yaml

```yaml
images:
  service:
    repository: atnog-harbor.av.it.pt/dt4mob/dt4mob-fire-simulator
    tag: latest
  rasterJob:
    repository: atnog-harbor.av.it.pt/dt4mob/dt4mob-raster-prepare-fire-simulator
    tag: latest

service:
  config:
    riskyAreasPath: "/data/raw/brisa_zones.geojson"
    forefireBin: "/usr/local/bin/forefire"
    ditto:
      apiUrl: "http://dt4mob-ditto-gateway:8080"
    s3:
      UrlInternal: "http://seaweed-s3.seaweedfs.svc.cluster.local:8333"
      UrlExternal: "http://localhost:8333"
    secret:
      # existingSecret: 
      userName: "user"
      password: "password"
    auth:
      url: "https://dt4mob-keycloak:8443/auth/realms/dt4mob/protocol/openid-connect/token"
      clientId: "ditto"

storage:
  className: local-path
  size: 20Gi
```

### Implantar

```bash
helm install dt4mob . -f my-values.yaml
```

### O que é implantado

| Recurso | Descrição |
|---------|-----------|
| **Deployment** | Contentor da API de ingestão (com contentor init à espera de rasters) |
| **Service** | Expõe porta 8080 dentro do cluster |
| **Job** | `prepare_rasters.py` — transfere DEM + COSc, produz ficheiros COG, escreve marcador `.done` |
| **PVC** | Volume partilhado de 20Gi para rasters + dados de simulação |
| **Secret** | Credenciais Ditto (nome de utilizador/palavra-passe) |
| **S3Identity** | Utilizador SeaweedFS para acesso S3 |
| **S3Credentials** | Chaves de acesso/secreta S3 geradas automaticamente |
| **S3Policy + Binding** | Políticas estilo IAM para acesso ao bucket (app + job) |

O contentor init no deployment aguarda o ficheiro `data/processed/.done` antes de o contentor principal iniciar, garantindo que os rasters estão prontos antes de processar ignições.

---

## Utilizar a Aplicação

### Colocar uma ignição

1. Abra a interface de mapa (ou envie um thing Ditto com `state: "new_ignition"`)
2. O sistema obtém o vento da estação meteorológica mais próxima via pesquisa espacial Ditto
3. Um cone de propagação é gerado (±15° de abertura, 4 × horizontes de 30 min)
4. O cone é verificado contra as zonas da autoestrada Brisa

### Se o cone intersectar uma zona Brisa

O pipeline ForeFire completo é executado automaticamente:

1. As estações meteorológicas são consultadas no Ditto (5 mais próximas)
2. A interpolação IDW produz grelhas de vento u/v
3. `landscape.nc` é construído a partir dos rasters COG DEM + combustível (reprojetados para EPSG:3763)
4. `fuels.csv` é escrito com parâmetros Rothermel
5. Um script ForeFire `.ff` é gerado
6. O ForeFire é executado como subprocesso (8 etapas × 15 min = horizonte de 2 h)
7. Os perímetros das etapas são analisados a partir dos ficheiros `step_NNNN.geojson`
8. Modelos 3D GLTF são gerados a partir dos perímetros e do cone
9. Todos os ficheiros são enviados para S3
10. O thing Ditto é atualizado com os resultados

### Estados

| Estado | Significado |
|--------|-------------|
| `new_ignition` | Evento de ignição recebido, a processar |
| `no_risk` | O cone não intersecta nenhuma zona de autoestrada |
| `simulating` | Simulação ForeFire em curso |
| `simulated` | Simulação concluída, resultados disponíveis |
| `simulation_failed` | O pipeline encontrou um erro |

### Ficheiros de saída

Cada simulação escreve em `data/simulations/{thing_id}/`:

| Ficheiro | Descrição |
|----------|-----------|
| `landscape.nc` | Grelha NetCDF de terreno + combustível + vento |
| `fuels.csv` | Parâmetros do modelo de combustível Rothermel |
| `forefire_script.ff` | Script de comandos ForeFire |
| `cone_horizon.geojson` | Cone de propagação rápida |
| `step_0015.geojson` ... `step_0120.geojson` | 8 timesteps de perímetro |
| `fire_cone.glb` | Modelo 3D do cone |
| `fire_simulation.glb` | Modelo 3D dos perímetros |

---

## Resolução de Problemas

**Simulação permanece em `simulating` indefinidamente**
Verifique os logs de ingestão:
```bash
docker compose logs -f ingestion
```
Causas comuns:
- Dados de vento indisponíveis (nenhuma estação Ditto encontrada)
- Binário ForeFire não encontrado (verifique `FOREFIRE_BIN`)
- Ficheiros raster em falta (execute `docker compose exec ingestion python prepare_rasters.py` primeiro)

**A preparação de rasters falha**
Verifique se os ficheiros DEM e COSc estão nos diretórios corretos e são válidos:
```bash
docker compose exec ingestion gdalinfo data/dem/dem.tif
```

**Nenhum dado de vento retornado**
O Ditto deve ter things de estações meteorológicas com funcionalidades de meteorologia:
```bash
curl -s http://localhost:8000/api/wind | python3 -m json.tool
```
Se vazio, verifique a conectividade com o Ditto e a disponibilidade dos dados das estações.

**Os uploads S3 falham**
Verifique as credenciais S3 e o URL do endpoint. O sistema usa boto3 com endereçamento path-style.

---

## Estrutura de Dados

```
app_deploy/data/
├── dem/                  # Rasters DEM de origem (coloque ficheiros .tif aqui)
├── cos/                  # Rasters de ocupação do solo COSc de origem
├── raw/
│   └── brisa_zones.geojson   # Polígonos da autoestrada Brisa
├── processed/            # Rasters COG (gerados, gitignored)
│   ├── dem.tif
│   ├── slope.tif
│   ├── aspect.tif
│   └── fuel_type.tif
└── simulations/          # Ficheiros ForeFire por execução (gitignored)
    └── {thing_id}/
        ├── landscape.nc
        ├── fuels.csv
        ├── forefire_script.ff
        ├── cone_horizon.geojson
        ├── step_0015.geojson ... step_0120.geojson
        ├── fire_cone.glb
        └── fire_simulation.glb
```
