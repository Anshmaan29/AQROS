"""Verified artifact-download endpoint for a ``Model_Version``.

``{model_name}`` in this path is the full composite ``{dataset_name}__{model_type}``
string carried over from the Training Pipeline.

The Registry is the single source of truth for a registered model's bytes: the
artifact is read back from the Registry's *own* ``Artifact_Store`` and served
only after both integrity guarantees are re-established on the stored bytes —
its signature is verified (where signing is configured) and its checksum is
recomputed and compared against the recorded ``Model_Checksum`` — never
re-contacting the Training Pipeline (Requirements 7.4, 7.5, 8.2, 8.3, 19.5,
1.4). Per design.md Section 13 a missing ``(model_name, version)`` or missing
stored bytes surface a typed ``404`` (Requirement 19.10), while a stored-artifact
integrity or signature failure is refused with a ``500`` (server-side
corruption). This module mirrors ``aqros_training_pipeline``'s artifact-download
route and never imports ``aqros_training_pipeline``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from aqros_model_registry.api.deps import get_registry_query_service
from aqros_model_registry.domain.integrity import ArtifactIntegrityError
from aqros_model_registry.domain.ports import SignatureVerificationError
from aqros_model_registry.domain.services import (
    ModelVersionNotFoundError,
    RegistryQueryService,
)

router = APIRouter(prefix="/v1/models", tags=["artifacts"])


@router.get("/{model_name}/versions/{version}/artifact")
async def download_artifact(
    model_name: str,
    version: int,
    service: RegistryQueryService = Depends(get_registry_query_service),
) -> Response:
    """Stream the integrity-verified ``Model_Artifact`` bytes for one version.

    The query service resolves the ``Model_Version``, reads the stored bytes
    back from the Registry's ``Artifact_Store``, verifies the signature, and
    recomputes and compares the checksum against the recorded
    ``Model_Checksum`` before returning — refusing to serve on any integrity
    failure (Requirements 7.4, 7.5, 8.2, 8.3, 19.5). Failures map to their HTTP
    surface per design.md Section 13: an unknown ``(model_name, version)`` or
    missing stored bytes to ``404`` (Requirement 19.10); a stored-artifact
    checksum mismatch or signature-verification failure to ``500`` since it is
    server-side corruption. The verified bytes are returned as an
    ``application/octet-stream`` attachment.
    """
    try:
        data = await service.get_artifact(model_name, version)
    except ModelVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No artifact bytes for '{model_name}' v{version}",
        ) from exc
    except ArtifactIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    except SignatureVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="model.joblib"'},
    )
