"""Extracted native application runtime; provenance in extraction.json."""
from pathlib import Path
import os,json


SHELL_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-unified@main#subdirectory=behaviors/unified-shell.yaml"


IMAGEGEN_BEHAVIOR_URI = "git+https://github.com/microsoft/amplifier-bundle-imagegen@main#subdirectory=behaviors/imagegen.yaml"


DEFAULT_BEHAVIORS = {
    SHELL_BEHAVIOR_URI: "Unified shell",
    IMAGEGEN_BEHAVIOR_URI: "Image generation",
}


def resource_root():
    packaged = Path(__file__).parent / "bundle_data"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parent.parent


def app_behaviors(settings):
    bundle = settings.get("bundle", {})
    metadata = settings.get("web_bundles", {})
    disabled = set(metadata.get("excluded", []))
    disabled.update(row.get("uri") for row in metadata.get("entries", [])
                    if row.get("enabled") is False)
    features=json.loads(os.environ.get('AMPLIFIER_ACP_HOST_FEATURES','{}'))
    enabled={SHELL_BEHAVIOR_URI:features.get('unifiedShell') is True,IMAGEGEN_BEHAVIOR_URI:features.get('imageGeneration') is True}
    if "app" in bundle:
        behaviors = list(bundle["app"])
        # Unified's older saved lists predate image tools. Only the negotiated
        # product host may add this default; explicit removal still wins.
        if enabled[IMAGEGEN_BEHAVIOR_URI] and IMAGEGEN_BEHAVIOR_URI not in disabled and IMAGEGEN_BEHAVIOR_URI not in behaviors:
            behaviors.append(IMAGEGEN_BEHAVIOR_URI)
        return behaviors
    return [uri for uri in DEFAULT_BEHAVIORS if uri not in disabled and enabled[uri]]


def resolve_builtin_behavior(uri):
    if uri == SHELL_BEHAVIOR_URI:
        # Keep the repository boundary explicit for Foundation namespace lookup.
        return resource_root().as_uri() + "#subdirectory=behaviors/unified-shell.yaml"
    return uri
