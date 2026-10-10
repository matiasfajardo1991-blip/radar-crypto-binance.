
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Radar: analisis solamente, sin operaciones reales.
BASE_URLS = [
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api-gcp.binance.com",
    "https://api.binance.com",
]

MIN_VOLUME_24H = 2_000_000
MAX_SYMBOLS = 15
REQUEST_TIMEOUT = 15


def api_get(path, params=None):
    query = urllib.parse.urlencode(params or {})
    last_error = None

    for base_url in BASE_URLS:
        url = base_url + path
        if query:
            url += "?" + query

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "RadarCryptoBinance/2.0"}
        )

        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT
            ) as response:
                data = response.read().decode("utf-8")
                print(f"API OK: {base_url}")
                return json.loads(data)

        except urllib.error.HTTPError as error:
            detail = error.read().decode(
                "utf-8", errors="replace"
            )
            last_error = (
                f"HTTP {error.code} en {base_url}: {detail}"
            )
            print("API ERROR:", last_error)

            # Try another official endpoint for access/server errors.
            if error.code not in (403, 429, 451) and error.code < 500:
                raise

        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = f"{base_url}: {error}"
            print("API ERROR:", last_error)

        except json.JSONDecodeError as error:
            last_error = f"JSON invalido en {base_url}: {error}"
            print("API ERROR:", last_error)

    raise RuntimeError(
        "No se pudo consultar ningun endpoint de Binance. "
        f"Ultimo error: {last_error}"
    )


def get_candidates():
    tickers = api_get("/api/v3/ticker/24hr")

    excluded = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
    candidates = []

    for item in tickers:
        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        if any(symbol.endswith(x) for x in excluded):
            continue

        try:
            volume = float(item.get("quoteVolume", 0))
            price = float(item.get("lastPrice", 0))
            change24 = float(item.get("priceChangePercent", 0))
        except (ValueError, TypeError):
            continue

        if volume >= MIN_VOLUME_24H and price > 0:
            candidates.append({
                "symbol": symbol,
                "price": price,
                "volume24h": volume,
                "change24h": change24
            })

    candidates.sort(
        key=lambda x: x["volume24h"],
        reverse=True
    )

    return candidates[:MAX_SYMBOLS]


def analyze(item):
    symbol = item["symbol"]

    candles = api_get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": "5m", "limit": 13}
    )

    # Ignore the current unfinished candle.
    candles = candles[:-1]

    if len(candles) < 12:
        return None

    previous = candles[-12:-6]
    recent = candles[-6:]

    first_open = float(recent[0][1])
    last_close = float(recent[-1][4])

    if first_open <= 0:
        return None

    # Change across the six completed 5-minute candles.
    change30m = (last_close / first_open - 1) * 100

    # Change of the latest completed 5-minute candle.
    candle_open = float(recent[-1][1])
    if candle_open <= 0:
        return None

    change5m = (
        float(recent[-1][4]) / candle_open - 1
    ) * 100

    previous_volume = sum(float(c[7]) for c in previous)
    recent_volume = sum(float(c[7]) for c in recent)

    volume_ratio = (
        recent_volume / previous_volume
        if previous_volume > 0 else 0
    )

    score = 0

    if change5m >= 0.5:
        score += 1
    if change30m >= 1:
        score += 1
    if volume_ratio >= 1.5:
        score += 1
    if volume_ratio >= 2:
        score += 1

    signal = (
        change30m >= 1 and volume_ratio >= 1.5
    ) or (
        change5m >= 0.5 and volume_ratio >= 2
    )

    return {
        "symbol": symbol,
        "price": item["price"],
        "change5m_pct": round(change5m, 3),
        "change30m_pct": round(change30m, 3),
        "change24h_pct": round(item["change24h"], 2),
        "volume_ratio": round(volume_ratio, 2),
        "score": score,
        "signal": signal
    }


def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram no configurado; se omiten alertas.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode()

    request = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT
        ) as response:
            response.read()
        print("Alerta enviada por Telegram.")
    except Exception as error:
        print("No se pudo enviar Telegram:", error)


def main():
    now = datetime.now(timezone.utc).isoformat()

    print(f"\nRADAR CRYPTO BINANCE | {now}")
    print("Modo: analisis solamente. No ejecuta operaciones.")

    try:
        candidates = get_candidates()
    except Exception as error:
        print(f"Error consultando Binance: {error}")
        return

    results = []

    for item in candidates:
        try:
            result = analyze(item)

            if result:
                results.append(result)
                print(
                    f"{result['symbol']} | "
                    f"5m: {result['change5m_pct']}% | "
                    f"30m: {result['change30m_pct']}% | "
                    f"Volumen: x{result['volume_ratio']} | "
                    f"Puntos: {result['score']}/4"
                )

            time.sleep(0.15)

        except Exception as error:
            print(f"Error analizando {item['symbol']}: {error}")

    results.sort(
        key=lambda x: (
            x["signal"], x["score"], x["change30m_pct"]
        ),
        reverse=True
    )

    signals = [r for r in results if r["signal"]]

    os.makedirs("radar_data", exist_ok=True)

    report = {
        "timestamp_utc": now,
        "mode": "analysis_only",
        "signals": signals,
        "results": results
    }

    with open(
        "radar_data/latest.json", "w", encoding="utf-8"
    ) as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    print(f"\nMonedas analizadas: {len(results)}")
    print(f"Senales detectadas: {len(signals)}")

    if signals:
        lines = [
            "Radar Crypto Binance - posibles movimientos:"
        ]

        for result in signals[:10]:
            lines.append(
                f"{result['symbol']}: "
                f"30m {result['change30m_pct']}%, "
                f"volumen x{result['volume_ratio']}, "
                f"puntos {result['score']}/4"
            )

        message = "\n".join(lines)
        print(message)
        send_telegram(message)
    else:
        print("Sin senales que cumplan los criterios actuales.")

    print("\nFin del analisis.")


if __name__ == "__main__":
    main()
