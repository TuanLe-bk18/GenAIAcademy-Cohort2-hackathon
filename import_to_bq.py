"""Backward-compatible entrypoint for non-destructive GCP provisioning."""

from infra.provision_gcp import main


if __name__ == "__main__":
    main()
