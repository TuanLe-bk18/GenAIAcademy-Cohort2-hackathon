"""Ingest real zone-level Open-Meteo observations into GCS and BigQuery."""

from heatsafe.ingestion import WeatherIngestionService


if __name__ == "__main__":
    rows = WeatherIngestionService().run()
    print(f"Ingested {len(rows)} weather observations with GCS lineage")
