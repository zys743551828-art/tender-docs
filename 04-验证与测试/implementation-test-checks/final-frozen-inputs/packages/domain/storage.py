"""真实S3私有对象适配。"""

import os

import boto3
from botocore.config import Config

from packages.domain.runtime import DomainError, required


def client():
    if os.environ.get("OBJECT_STORE_BACKEND") != "s3":
        raise DomainError("OBJECT_STORE_NOT_CONFIGURED", "尚未配置受支持的对象存储", 503)
    return boto3.client(
        "s3",
        endpoint_url=required("OBJECT_STORE_ENDPOINT"),
        aws_access_key_id=required("OBJECT_STORE_ACCESS_KEY"),
        aws_secret_access_key=required("OBJECT_STORE_SECRET_KEY"),
        region_name=os.environ.get("OBJECT_STORE_REGION", "us-east-1"),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


def bucket():
    return required("OBJECT_STORE_BUCKET")


def get(key):
    return client().get_object(Bucket=bucket(), Key=key)["Body"].read()


def put(key, data, media):
    return client().put_object(Bucket=bucket(), Key=key, Body=data, ContentType=media)


def sign(key, method="get_object", extra=None, seconds=900):
    return client().generate_presigned_url(
        method, Params={"Bucket": bucket(), "Key": key, **(extra or {})}, ExpiresIn=seconds
    )
