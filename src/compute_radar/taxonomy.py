"""
The 8-layer compute-stack taxonomy every tracked startup is classified against.

Six of the eight layers come directly from the CDL Next Gen Computing investment thesis
(Post-Classical Compute -> physics_substrate/new_architecture; Systems and Integration ->
codesign_eda/chiplet_interconnect/advanced_packaging; Sovereign Deployment ->
sovereign_cloud/sovereign_edge_onprem). `power_thermal` was split out as its own layer
rather than left as a sub-bullet of advanced_packaging: a teardown of where AI-infrastructure
capital actually goes (see the project's case-study notes) found liquid cooling and power
delivery to be a large and fast-growing cost center in their own right, not a footnote to
packaging - and one of the CDL-NGC example ventures (a liquid-cooling company) doesn't have
a clean home in the other seven layers without it.

A startup can and often should carry more than one tag - e.g. a photonic neuromorphic chip
for edge robotics is both `physics_substrate` and `sovereign_edge_onprem`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Layer:
    key: str
    label: str
    description: str
    thesis_vertical: str


TAXONOMY: dict[str, Layer] = {
    "physics_substrate": Layer(
        key="physics_substrate",
        label="Physics & substrate",
        description=(
            "Beyond CMOS silicon: photonic, quantum, spintronic, cryogenic, and compound "
            "semiconductor approaches where the compute medium itself changes the "
            "performance ceiling."
        ),
        thesis_vertical="Post-Classical Compute",
    ),
    "new_architecture": Layer(
        key="new_architecture",
        label="New compute architectures",
        description=(
            "Beyond von Neumann: neuromorphic computing, memory-centric architectures, "
            "and RISC-V based sovereign silicon."
        ),
        thesis_vertical="Post-Classical Compute",
    ),
    "codesign_eda": Layer(
        key="codesign_eda",
        label="Co-design / EDA / test",
        description=(
            "Architecture-aware design, EDA tooling, AI-assisted chip design, "
            "hardware-software co-optimization, and test & yield qualification."
        ),
        thesis_vertical="Systems and Integration",
    ),
    "chiplet_interconnect": Layer(
        key="chiplet_interconnect",
        label="Chiplet & interconnect",
        description=(
            "Die-to-die interconnects, UCIe-compliant interfaces, co-packaged optics, "
            "and in-package memory."
        ),
        thesis_vertical="Systems and Integration",
    ),
    "advanced_packaging": Layer(
        key="advanced_packaging",
        label="Advanced packaging",
        description="2.5D and 3D integration, substrate and interposer technology.",
        thesis_vertical="Systems and Integration",
    ),
    "power_thermal": Layer(
        key="power_thermal",
        label="Power & thermal",
        description=(
            "Power delivery and liquid/immersion cooling for compute infrastructure - "
            "split out from advanced packaging because of its outsized share of real "
            "AI-infrastructure capex."
        ),
        thesis_vertical="Systems and Integration (elevated)",
    ),
    "sovereign_cloud": Layer(
        key="sovereign_cloud",
        label="Sovereign cloud",
        description=(
            "HPC systems, sovereign AI factories, attested inference, and confidential "
            "compute for regulated European hyperscale and public-sector buyers."
        ),
        thesis_vertical="Sovereign Deployment",
    ),
    "sovereign_edge_onprem": Layer(
        key="sovereign_edge_onprem",
        label="Sovereign edge / on-prem",
        description=(
            "Sovereign-cloud-ready middleware, air-gapped deployment, hardware roots of "
            "trust, and always-on low-power inference for industrial, automotive, and "
            "defense use where cloud connectivity cannot be assumed."
        ),
        thesis_vertical="Sovereign Deployment",
    ),
}

LAYER_KEYS = list(TAXONOMY.keys())


def is_valid_layer(key: str) -> bool:
    return key in TAXONOMY


def describe_taxonomy_for_prompt() -> str:
    """Render the taxonomy as a numbered list for injection into the Analyst agent's prompt."""
    lines = []
    for layer in TAXONOMY.values():
        lines.append(f"- {layer.key}: {layer.label} — {layer.description}")
    return "\n".join(lines)
