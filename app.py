# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from fastapi import FastAPI, Body
import logging
import base64
import json
from pydantic import BaseModel

YEAR = 31_556_952


class Patch(BaseModel):
    op: str
    path: str = "/spec/template/spec"
    value: dict[str, int]


app = FastAPI()

webhook = logging.getLogger(__name__)
webhook.setLevel(logging.INFO)
logging.basicConfig(format="[%(asctime)s] %(levelname)s: %(message)s")


def patch_termination(existing_selector: bool) -> base64:
    webhook.info("Updating terminationGracePeriodSeconds, replacing it.")
    patch_operations = [
        Patch(
            op="replace" if existing_selector else "add",
            value={"terminationGracePeriodSeconds": YEAR},
        ).model_dump()
    ]
    return base64.b64encode(json.dumps(patch_operations).encode())


def admission_review(uid: str, message: str, existing_selector: bool) -> dict:
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": uid,
            "allowed": True,
            "patchType": "JSONPatch",
            "status": {"message": message},
            "patch": patch_termination(existing_selector).decode(),
        },
    }


def admission_validation(uid: str, current_value: int):
    if current_value < 31:
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
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": uid,
            "allowed": True,
            "status": {f"Valid value has been provided ({current_value}s)"},
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
    period_value = int(selector["terminationGracePeriodSeconds"])

    return admission_validation(uid, period_value)
