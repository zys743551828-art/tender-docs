"""Initialize a private bucket on the local S3-compatible development server."""

from __future__ import annotations

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from initialize_database import local_configuration


def main() -> None:
    values = local_configuration()
    client = boto3.client(
        "s3",
        endpoint_url=values["OBJECT_STORE_ENDPOINT"],
        aws_access_key_id=values["OBJECT_STORE_ACCESS_KEY"],
        aws_secret_access_key=values["OBJECT_STORE_SECRET_KEY"],
        region_name=values["OBJECT_STORE_REGION"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
        ),
    )
    bucket = values["OBJECT_STORE_BUCKET"]
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        raise SystemExit(
            "Private bucket missing. Create it using the storage administrator first."
        ) from None
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    client.put_bucket_cors(
        Bucket=bucket,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": [values["APP_ORIGIN"]],
                    "AllowedMethods": ["GET", "PUT", "HEAD"],
                    "AllowedHeaders": ["*"],
                    "ExposeHeaders": ["ETag", "x-amz-version-id"],
                    "MaxAgeSeconds": 600,
                }
            ]
        },
    )
    print("Private development bucket, versioning and allowed browser origin configured.")


if __name__ == "__main__":
    main()
