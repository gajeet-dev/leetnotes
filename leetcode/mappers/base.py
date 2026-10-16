from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..exceptions import ResponseShapeError

T = TypeVar("T", bound=BaseModel)


def build(model: type[T], **fields) -> T:
    try:
        return model(**fields)
    except ValidationError as exc:
        raise ResponseShapeError(f"{model.__name__}: {exc}") from exc
