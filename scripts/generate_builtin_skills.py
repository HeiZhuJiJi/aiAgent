import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILTIN_SKILLS = ("web-research", "document-analysis", "grounded-research")
OUTPUT_DIRECTORY = ROOT / "apps" / "web" / "public" / "builtin-skills"
MEMBERS = (
    "skill.yaml",
    "instructions.md",
    "workflow.yaml",
    "schemas/input.schema.json",
    "schemas/output.schema.json",
    "policies/permissions.yaml",
    "eval/cases.json",
)


def main() -> None:
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for skill_name in BUILTIN_SKILLS:
        source = ROOT / "skills" / skill_name
        output = OUTPUT_DIRECTORY / f"{skill_name}.skill.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in MEMBERS:
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, (source / name).read_bytes())


if __name__ == "__main__":
    main()
