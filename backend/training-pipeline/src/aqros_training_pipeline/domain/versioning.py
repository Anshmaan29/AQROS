"""The Model_Versioner — monotonic, per-composite-name version assignment.

Pure logic over the ``TrainedModelRepository`` port. A model's "name" is the
composite ``f"{dataset_name}__{model_type}"`` string (Key Design Decision 3),
so two different ``dataset_name``s produce two fully independent version
sequences even for the same ``ModelType``: training ``random_forest`` against
``aapl_5d_direction`` and against ``msft_20d_return`` each start at version 1
and never share or influence one another's counter (Requirement 8).
"""

from __future__ import annotations

from aqros_training_pipeline.domain.models import ModelType
from aqros_training_pipeline.domain.ports import TrainedModelRepository

MODEL_NAME_SEPARATOR = "__"
"""Separator joining ``dataset_name`` and ``model_type`` into a composite model name.

``dataset_name`` values must not themselves contain this separator (enforced
by the same name validation the Dataset Builder applies to dataset names).
"""


def build_model_name(dataset_name: str, model_type: ModelType) -> str:
    """Return the composite ``f"{dataset_name}__{model_type}"`` model name.

    This is the opaque key ``Model_Version`` is assigned against — never the
    bare ``ModelType`` value (Key Design Decision 3).
    """
    return f"{dataset_name}{MODEL_NAME_SEPARATOR}{model_type.value}"


async def assign_version(model_name: str, repository: TrainedModelRepository) -> int:
    """Return the next ``Model_Version`` for ``model_name``.

    Calls ``repository.get_latest_version(model_name)``; returns ``1`` when
    that is ``None`` (a brand-new composite name), else the existing maximum
    plus one (Requirement 8.1/8.2). Because two distinct composite names
    have distinct version sequences, versioning for one dataset name never
    interacts with another's, even for the same ``ModelType``.
    """
    latest = await repository.get_latest_version(model_name)
    if latest is None:
        return 1
    return latest + 1
