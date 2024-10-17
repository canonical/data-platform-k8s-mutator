# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from fastapi import FastAPI, Body
import logging
import base64
from pydantic import BaseModel, TypeAdapter
import os

# Default to 1 year
YEAR = 31_556_952
GRACE_PERIOD: int = int(os.getenv("GRACE_PERIOD_SECONDS", YEAR))


class Patch(BaseModel):
    op: str
    path: str = "/spec/template/spec"
    value: dict[str, int]


app = FastAPI()

webhook = logging.getLogger(__name__)
webhook.setLevel(logging.INFO)
logging.basicConfig(format="[%(asctime)s] %(levelname)s: %(message)s")

ADAPTER = TypeAdapter(list[Patch])


def patch_termination(existing_value: bool) -> str:
    webhook.info("Updating terminationGracePeriodSeconds, replacing it.")
    patch_operations = [
        Patch(
            op="replace" if existing_value else "add",
            value={"terminationGracePeriodSeconds": GRACE_PERIOD},
        )
    ]
    return base64.b64encode(ADAPTER.dump_json(patch_operations)).decode()


def admission_review(uid: str, message: str, existing_value: bool) -> dict:
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": uid,
            "allowed": True,
            "patchType": "JSONPatch",
            "status": {"message": message},
            "patch": patch_termination(existing_value),
        },
    }


def admission_validation(uid: str, current_value: int | None):
    if not current_value or current_value > 30:
        return {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {
                "uid": uid,
                "allowed": True,
                "status": {
                    "message": f"Valid value has been provided ({current_value})"
                },
            },
        }
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": uid,
            "allowed": False,
            "status": {
                "code": 403,
                "message": f"Termination period lower than 30s is not allowed (given {current_value})",
            },
        },
    }


@app.post("/mutate")
def mutate_request(request: dict = Body(...)):
    uid = request["request"]["uid"]
    selector = request["request"]["object"]["spec"]["template"]["spec"]

    return admission_review(
        uid,
        "Successfully updated terminationGracePeriodSeconds.",
        True if "terminationGracePeriodSeconds" in selector else False,
    )


@app.post("/validate")
def validate_request(request: dict = Body(...)):
    uid = request["request"]["uid"]
    selector = request["request"]["object"]["spec"]["template"]["spec"]
    period_value = selector.get("terminationGracePeriodSeconds")

    return admission_validation(uid, period_value)
