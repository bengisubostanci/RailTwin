# RailTwin — Raylı Sistemler için Gerçek Zamanlı Dijital İkiz

RailTwin, bir metro hattını (trenler, ray segmentleri ve makaslar) canlı bir dijital ikiz olarak yansıtır.
Bir saha simülatörü sensör verilerini MQTT üzerinden akıtır. Backend, sağlıklı davranışın nasıl
göründüğünü öğrenir, her varlığı gerçek zamanlı olarak puanlar ve bir arıza oluşmadan önce alarm üretir.
Senaryo analizi (Ya olursa?) motoru ise işletmedeki değişikliklerin önümüzdeki haftalarda nasıl
sonuçlanacağını gösterir.

[![CI](https://github.com/bengisubostanci/RailTwin/actions/workflows/ci.yml/badge.svg)](https://github.com/bengisubostanci/RailTwin/actions/workflows/ci.yml)
![stack](https://img.shields.io/badge/stack-FastAPI%20·%20TimescaleDB%20·%20Redis%20·%20MQTT%20·%20React-3fb7ff)

## Neler yapar

| | |
|---|---|
| **Canlı izleme** | Trenlerin gerçek zamanlı hareket ettiği hat şeması, KPI'lar, varlık başına sensör grafikleri ve alarm akışı. |
| **Kestirimci bakım** | Varlık başına ML sağlık puanı (%0–100), olası arızalı bileşen, eğilime dayalı kritiğe kalan süre tahmini ve önceliklendirilmiş iş kuyruğu. |
| **Arıza enjeksiyonu** | Arayüzden bir bozulma (rulman aşırı ısınması, tekerlek düzlüğü, fren kaçağı, ray kusuru, makas aşınması, …) enjekte edin ve modelin onu yakalamasını izleyin. Ardından gidermek için bakım ekibi gönderin. |
| **Senaryo analizi** | Sefer aralığını, filo büyüklüğünü, talebi, hava durumunu, hat hızını veya bakım stratejisini değiştirin; dakiklik, arızalar, hat aşınması, enerji, CO₂ ve maliyeti bugünkü mevcut durumla karşılaştırın (Monte Carlo). |

## Mimari

```
 ┌──────────────┐  MQTT   ┌───────────┐  MQTT  ┌──────────────────────────────┐  WS/REST  ┌──────────┐
 │  simulator   │───────▶ │ mosquitto │──────▶ │ backend (FastAPI)            │─────────▶ │ frontend │
 │ (twin_core   │◀─────── │           │◀────── │  · alım → TimescaleDB        │           │ React +  │
 │  fiziği)     │ kontrol └───────────┘        │  · sağlık puanlama (sklearn) │           │ Recharts │
 └──────────────┘                              │  · alarmlar, iş emirleri     │           └──────────┘
                                               │  · senaryo motoru            │
                                               │  · canlı görüntü → Redis     │
                                               └──────────────────────────────┘
```

* **`twin_core/`**: ortak fizik motoru: hat topolojisi, cer gücü sınırlı tren kinematiği, ısıl ve
  titreşim sensör modelleri ve yavaşça artan bozulma arızaları. Simülatör bunu gerçek zamanlı çalıştırır.
  Backend ise eğitim referansını oluşturmak için aynı motoru arayüzsüz ve arızasız olarak çalıştırır.
* **`simulator/`**: hat kenarı ve araç üstü IoT ağ geçitlerinin yerini tutar. Saniyede bir
  `railtwin/telemetry/{type}/{id}` yayımlar (simüle edilen zaman varsayılan olarak ×5 hızda akar) ve
  `railtwin/control` konusunu dinler. Gerçek arıza bilgisini asla yayımlamaz; backend arızaları kendisi
  tespit etmek zorundadır.
* **`backend/`**: FastAPI, asyncpg (saklama politikalı TimescaleDB hypertable), Redis anlık görüntü
  önbelleği ve WebSocket yayını.
* **`frontend/`**: React + TypeScript + Vite; Docker içinde nginx tarafından sunulur.

### Anomali tespiti nasıl çalışır

Model her sensör için, **o sensörü fiziksel olarak etkileyen işletme koşullarından beklenen değeri**
öğrenir: aks rulmanı sıcaklığını ortalama hız, yük ve ortam sıcaklığından; cer motoru akımını cer
gücünden; ray titreşimini geçiş hızından; vb. Regresör, doğrusal bir eğilim ile gradyan artırmalı
ağaçların birleşimidir. Doğrusal eğilim fiziği nadir görülen koşullara taşır, ağaçlar ise doğrusal
olmayan etkileri yakalar. İkinci bir model her koşul için beklenen gürültü düzeyini öğrenir; böylece
artıklar kalibre edilmiş z-skorlarına dönüşür.

Sağlık puanı şunları birleştirir:

* *kötü* yöndeki en büyük artık (daha sıcak rulman, daha düşük fren basıncı, …); bu aynı zamanda olası
  sebebi de belirtir;
* tek bir sensörün işaretlemediği kombinasyonlar için tüm artık vektörü üzerinde bir IsolationForest.

Tepe değeri tutma ve yumuşatma, aralıklı kanıtların etkisini korur; böylece yalnızca üzerinden tren
geçerken görünen bir ray kusuru da hesaba katılır. Referans, hava durumu ve talep için tabakalı bir
ızgarayı kapsar; bu yüzden sıcaklık kaydırıcısını −15 °C'den 45 °C'ye çevirmek yanlış alarm üretmez.
Testler bunu ve her arıza türünün tespitini ve doğru sebebe atanmasını kontrol eder.

## Hızlı başlangıç

Gereksinimler: Docker Desktop.

```bash
docker compose up --build
```

**http://localhost:8080** adresini açın. İlk açılışta backend modellerini eğitir (~20 sn) ve bir
volume'da önbelleğe alır; sonraki açılışlar anında gerçekleşir. İlk çalıştırmada trenler, rulman
modelinin dayandığı hareketli ortalamalar geçmiş veri toplarken yaklaşık dört dakika boyunca *ısınıyor*
olarak görünür. Yeniden başlatmadan sonra backend bu ortalamaları TimescaleDB'den yeniden oluşturur ve
hemen puanlamaya başlar. Kendiliğinden oluşan arızalar iki dakikalık ısınma süresinden sonra başlar.

| Servis | URL |
|---|---|
| Gösterge paneli | http://localhost:8080 |
| API belgeleri (Swagger) | http://localhost:8000/docs |
| MQTT broker | `localhost:1883` |
| TimescaleDB | `localhost:5433` (railtwin / railtwin) |

Ayarlar (ortam değişkenleri, ör. bir `.env` dosyasında):

| Değişken | Varsayılan | Anlamı |
|---|---|---|
| `TIME_SCALE` | `5` | Hattın gerçek zamandan kaç kat hızlı çalıştığı |
| `SPONTANEOUS_FAULTS` | `true` | Varlıkların kendiliğinden bozulmasına izin ver |
| `FAULT_INTERVAL_S` | `300` | Kendiliğinden oluşan arızalar arasındaki ortalama gerçek saniye (aynı anda en fazla 2) |

### İki dakikalık demo

1. **Genel Bakış**: trenlerin hareketini izleyin. Sıcaklık kaydırıcısını 40 °C'ye çekin; rulman
   sıcaklıkları yükselir, ancak model bunu beklediği için sağlık yeşil kalır.
2. Bir tren açın (ör. **T-103**), *Rulman aşırı ısınması*'nı seçin ve **Arıza enjekte et**'e tıklayın.
   Bir iki dakika içinde sağlık düşer, olası sebep *aks rulmanı sıcaklığı* olarak görünür ve bir ML
   alarmı belirir. Kural tabanlı alarm ise ancak daha sonra, mutlak sınır aşıldığında tetiklenir.
3. **Bakım**: varlık, önerilen işlemle birlikte kuyruğun en üstüne çıkar. **Gönder**'e tıklayın; sağlık
   toparlanır.
4. **Senaryolar**: *Yoğun saat artışı* veya *Sıcak hava dalgası*'nı deneyin, ardından stratejiyi
   *Kestirimci* olarak değiştirin.

## Yerel geliştirme

```bash
python -m venv .venv
.venv/Scripts/pip install -e twin_core -r backend/requirements.txt pytest   # macOS/Linux'ta bin/
cd backend && ../.venv/Scripts/python -m pytest
```

Python 3.11 veya üstü gerekir (simülatör `except*` sözdizimini kullanır; Docker imajları 3.12 ile gelir).

GitHub Actions her push'ta ve pull request'te üç kontrol çalıştırır: backend testleri, frontend tip
kontrolü ve derlemesi, tüm Docker imajlarının derlenmesi ([.github/workflows/ci.yml](.github/workflows/ci.yml)).

Altyapıyı Docker'da, uygulamaları yerelde çalıştırın:

```bash
docker compose up timescaledb redis mosquitto
cd backend && DATABASE_URL=postgresql://railtwin:railtwin@localhost:5433/railtwin ../.venv/Scripts/uvicorn app.main:app --reload
cd simulator && ../.venv/Scripts/python main.py
cd frontend && npm install && npm run dev   # http://localhost:5173, /api ve /ws isteklerini yönlendirir
```

## API'ye genel bakış

| Yöntem | Yol | |
|---|---|---|
| GET | `/api/topology` | Hat yerleşimi |
| GET | `/api/state` | Canlı anlık görüntü (varlıklar, sağlık, KPI'lar, aktif alarmlar) |
| GET | `/api/assets/{id}/telemetry?minutes=15&bucket_s=5` | Zaman aralıklarına bölünmüş geçmiş |
| POST | `/api/assets/{id}/faults` | Bozulma enjekte et `{ "kind": "wheel_flat" }` |
| POST | `/api/assets/{id}/maintenance` | Bakım ekibi gönder (arızayı giderir, iş emri kaydeder) |
| POST | `/api/environment` | Sahadaki ortam sıcaklığını / yolcu talebini ayarla |
| GET | `/api/maintenance` | Önceliklendirilmiş kuyruk ve iş emirleri |
| POST | `/api/scenarios/run` | Senaryo analizi çalıştır |
| WS | `/ws` | Her saniye anlık görüntü ve alarm olayları |
