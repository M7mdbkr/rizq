"""Truthful local readiness information for ZATCA e-invoicing integration.

No onboarding, cryptographic signing, invoice submission or filing is performed here.
"""
from __future__ import annotations

import os


def readiness() -> dict:
    mode = os.environ.get('RIZQ_ZATCA_ENV', 'unconfigured')
    if mode not in ('sandbox', 'simulation', 'production'):
        mode = 'unconfigured'
    return {
        'environment': mode,
        'official_fatoora_connected': False,
        'invoice_submission_implemented': False,
        'local_vat_reports': True,
        'checklist': [
            {'id': 'environment', 'ready': mode != 'unconfigured', 'detail': 'Select the official sandbox, simulation or production environment.'},
            {'id': 'egs_onboarding', 'ready': False, 'detail': 'Onboard the EGS unit and obtain its CSID and secret through ZATCA.'},
            {'id': 'invoice_xml', 'ready': False, 'detail': 'Generate and cryptographically sign compliant invoice XML.'},
            {'id': 'sdk_validation', 'ready': False, 'detail': 'Validate invoice examples with the official ZATCA SDK.'},
            {'id': 'reporting_clearance', 'ready': False, 'detail': 'Implement and verify reporting and clearance against the selected environment.'},
        ],
        'message_ar': 'تقارير الضريبة محلية فقط؛ ربط فاتورة وإرسال الفواتير لم يُنجزا بعد.',
    }
