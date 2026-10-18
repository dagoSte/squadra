"""Keep both client packages at the release version and rooted at this skill."""
import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/dagoSte/squadra"
AUTHOR = "Stefano D'Agostino"


class PluginPackage(unittest.TestCase):
    def document(self, relative):
        path = ROOT / relative
        self.assertTrue(path.is_file(), f"Missing package file: {relative}")
        self.assertFalse(path.is_symlink(), relative)
        value = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def test_manifest_metadata_and_versions_follow_registry(self):
        module = ast.parse((ROOT / "scripts/registry.py").read_text(encoding="utf-8"))
        versions = [ast.literal_eval(node.value) for node in module.body
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "VERSION"
                        for target in node.targets)]
        self.assertEqual(len(versions), 1)
        for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            with self.subTest(manifest=relative):
                data = self.document(relative)
                self.assertEqual(data["name"], "squadra")
                self.assertEqual(data["version"], versions[0])
                self.assertIsInstance(data["description"], str)
                self.assertTrue(data["description"].strip())
                self.assertEqual(data["author"], {"name": AUTHOR})
                self.assertEqual(data["license"], "MIT")
                self.assertEqual(data["repository"], REPOSITORY)
                self.assertEqual(data["homepage"], REPOSITORY)

    def test_claude_marketplace_and_implicit_root_skill(self):
        data = self.document(".claude-plugin/marketplace.json")
        self.assertEqual(data["name"], "squadra")
        self.assertEqual(data["owner"], {"name": AUTHOR})
        self.assertIsInstance(data["description"], str)
        self.assertTrue(data["description"].strip())
        self.assertEqual(data["plugins"], [{"name": "squadra", "source": "."}])
        self.assertNotIn("skills", self.document(".claude-plugin/plugin.json"))
        self.assertTrue((ROOT / "SKILL.md").is_file())
        self.assertFalse((ROOT / "skills").exists())

    def test_codex_marketplace_shape_and_root_source(self):
        data = self.document(".agents/plugins/marketplace.json")
        self.assertEqual(data["name"], "squadra")
        self.assertEqual(data["interface"], {"displayName": "Squadra 3+"})
        self.assertEqual(data["plugins"], [{
            "name": "squadra",
            "source": {"source": "local", "path": "./"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }])

    def test_codex_skill_root_and_interface(self):
        data = self.document(".codex-plugin/plugin.json")
        # Codex 0.162.1 ignores bare './'; './.' is the measured root spelling.
        self.assertEqual(data["skills"], "./.")
        skill_root = (ROOT / data["skills"]).resolve()
        self.assertEqual(skill_root, ROOT.resolve())
        self.assertTrue((skill_root / "SKILL.md").is_file())
        interface = data["interface"]
        self.assertEqual(interface["displayName"], "Squadra 3+")
        self.assertEqual(interface["developerName"], AUTHOR)
        self.assertEqual(interface["category"], "Productivity")
        for key in ("shortDescription", "longDescription"):
            self.assertIsInstance(interface[key], str)
            self.assertTrue(interface[key].strip())
        prompts = interface["defaultPrompt"]
        self.assertIsInstance(prompts, list)
        self.assertGreaterEqual(len(prompts), 1)
        self.assertLessEqual(len(prompts), 3)
        for prompt in prompts:
            self.assertIsInstance(prompt, str)
            self.assertGreater(len(prompt.strip()), 0)
            self.assertLessEqual(len(prompt), 128)
        keywords = data["keywords"]
        self.assertIsInstance(keywords, list)
        self.assertGreaterEqual(len(keywords), 1)
        for keyword in keywords:
            self.assertIsInstance(keyword, str)
            self.assertTrue(keyword.strip())
        self.assertEqual(interface["capabilities"], ["Interactive", "Read", "Write"])
        self.assertEqual(interface["websiteURL"], REPOSITORY)
        self.assertEqual(interface["privacyPolicyURL"], f"{REPOSITORY}/blob/main/PRIVACY.md")
        self.assertEqual(interface["termsOfServiceURL"], f"{REPOSITORY}/blob/main/LICENSE")
        for name in ("PRIVACY.md", "LICENSE"):
            self.assertTrue((ROOT / name).is_file(), name)
        screenshots = interface["screenshots"]
        self.assertIsInstance(screenshots, list)
        self.assertGreaterEqual(len(screenshots), 1)
        for relative in (interface["composerIcon"], interface["logo"], *screenshots):
            self.assertTrue(relative.startswith("./"), relative)
            path = (ROOT / relative).resolve()
            self.assertTrue(path.is_relative_to(ROOT.resolve()), relative)
            self.assertTrue(path.is_file(), relative)

    def test_mit_license_has_owner_grant_and_disclaimer(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn(f"Copyright (c) 2026 {AUTHOR}", license_text)
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        self.assertIn("sublicense, and/or sell", license_text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)
        self.assertIn("THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE", license_text)

    def test_root_has_only_the_existing_agent_ui_metadata(self):
        for name in ("skills", "commands", "hooks", "output-styles", "themes",
                     "workflows", "monitors", "bin", ".mcp.json"):
            self.assertFalse((ROOT / name).exists(), name)
        self.assertEqual(sorted(str(path.relative_to(ROOT / "agents"))
                                for path in (ROOT / "agents").rglob("*")
                                if path.is_file()), ["openai.yaml"])
        for name in ("scripts", "references", "assets"):
            self.assertTrue((ROOT / name).is_dir(), name)


if __name__ == "__main__":
    unittest.main()
