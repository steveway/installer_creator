import uuid


def generate_random_uuid() -> str:
    """Generates a random UUID (version 4)."""
    return str(uuid.uuid4())


def generate_deterministic_uuid(input_string: str) -> str:
    """Generates a deterministic UUID (version 5) based on an input string using the DNS namespace."""
    if not isinstance(input_string, str):
        raise TypeError("Input must be a string")
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, input_string))
