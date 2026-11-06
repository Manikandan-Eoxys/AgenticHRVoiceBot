"""
tests/test_rag_extract_path.py

Unit tests for extract_path() and RagConfig in services/rag_retriever_service.py.
"""

import sys
import unittest
from pathlib import Path

# Ensure hr_voice_agent root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.rag_retriever_service import RagConfig, extract_path


class TestRagExtractPath(unittest.TestCase):
    def test_default_path(self):
        """Covers default path 'results[].content' extracting multiple snippets."""
        data = {
            "results": [
                {"content": "First document excerpt."},
                {"content": "Second document excerpt."},
            ]
        }
        res = extract_path(data, "results[].content")
        self.assertEqual(res, ["First document excerpt.", "Second document excerpt."])

    def test_nested_path(self):
        """Covers nested path 'data.matches[].text' traversal."""
        data = {
            "data": {
                "matches": [
                    {"text": "Match 1"},
                    {"text": "Match 2"},
                ]
            }
        }
        res = extract_path(data, "data.matches[].text")
        self.assertEqual(res, ["Match 1", "Match 2"])

    def test_single_string(self):
        """Covers path 'answer' extracting a single string value."""
        data = {"answer": "Annual leave entitlement is 24 days."}
        res = extract_path(data, "answer")
        self.assertEqual(res, ["Annual leave entitlement is 24 days."])

    def test_missing_key(self):
        """Covers missing keys at various depths being skipped safely without raising."""
        # Missing key in one item of a list
        data_partial = {
            "results": [
                {"content": "Valid item"},
                {"other_field": "Missing content"},
                {"content": "Another valid item"},
            ]
        }
        res_partial = extract_path(data_partial, "results[].content")
        self.assertEqual(res_partial, ["Valid item", "Another valid item"])

        # Missing top-level key entirely
        data_empty = {"status": "ok"}
        self.assertEqual(extract_path(data_empty, "results[].content"), [])

        # Missing intermediate key
        data_nested = {"data": {"other": []}}
        self.assertEqual(extract_path(data_nested, "data.matches[].text"), [])

    def test_empty_list(self):
        """Covers empty list handling at various levels."""
        # Empty list at target
        data = {"results": []}
        self.assertEqual(extract_path(data, "results[].content"), [])

        # Top-level empty list
        self.assertEqual(extract_path([], "[]"), [])

        # Nested empty list
        data_nested = {"data": {"matches": []}}
        self.assertEqual(extract_path(data_nested, "data.matches[].text"), [])

    def test_mixed_none_values(self):
        """Covers dropping None values, empty strings, and whitespace-only entries."""
        data = {
            "results": [
                {"content": None},
                {"content": ""},
                {"content": "   "},
                {"content": "Valid text"},
                None,
                {"content": None},
                {"content": "Another valid text"},
            ]
        }
        res = extract_path(data, "results[].content")
        self.assertEqual(res, ["Valid text", "Another valid text"])

        # Top-level list with None and empty values
        list_data = [None, "", "Snippet A", "   ", None, "Snippet B"]
        self.assertEqual(extract_path(list_data, "[]"), ["Snippet A", "Snippet B"])

    def test_non_dict_input(self):
        """Covers non-dict / invalid input types being handled safely without raising."""
        self.assertEqual(extract_path(None, "results[].content"), [])
        self.assertEqual(extract_path("raw string input", "results[].content"), [])
        self.assertEqual(extract_path(12345, "answer"), [])
        self.assertEqual(extract_path(True, "[]"), [])
        self.assertEqual(extract_path([1, 2, 3], "results[].content"), [])
        self.assertEqual(extract_path({}, ""), [])

    def test_top_level_list_path(self):
        """Covers top-level list fan-out path '[]'."""
        data = ["Snippet 1", "Snippet 2", "Snippet 3"]
        self.assertEqual(extract_path(data, "[]"), ["Snippet 1", "Snippet 2", "Snippet 3"])

    def test_non_string_conversion(self):
        """Covers non-string values being converted with str()."""
        data = {
            "results": [
                {"content": 100},
                {"content": 200.5},
                {"content": True},
            ]
        }
        res = extract_path(data, "results[].content")
        self.assertEqual(res, ["100", "200.5", "True"])


class TestRagConfig(unittest.TestCase):
    def test_defaults(self):
        """Test RagConfig default values."""
        cfg = RagConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.grounding_mode, "balanced")
        self.assertEqual(cfg.source_mode, "external")
        self.assertEqual(cfg.top_k, 3)
        self.assertEqual(cfg.url, "")
        self.assertEqual(cfg.method, "POST")
        self.assertEqual(cfg.auth_type, "none")
        self.assertEqual(cfg.auth_value, "")
        self.assertEqual(cfg.auth_name, "")
        self.assertEqual(cfg.query_key, "query")
        self.assertEqual(cfg.response_path, "results[].content")

    def test_from_knowledge_base_rag_enabled(self):
        """Test from_knowledge_base reading standard camelCase structure with ragEnabled."""
        kb = {
            "ragEnabled": True,
            "groundingMode": "strict",
            "sourceMode": "external",
            "topK": 5,
            "externalRag": {
                "url": "https://rag.example.com/search",
                "method": "POST",
                "authType": "bearer",
                "authValue": "token123",
                "authName": "",
                "queryKey": "question",
                "responsePath": "hits[].snippet",
            },
        }
        cfg = RagConfig.from_knowledge_base(kb)
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.grounding_mode, "strict")
        self.assertEqual(cfg.source_mode, "external")
        self.assertEqual(cfg.top_k, 5)
        self.assertEqual(cfg.url, "https://rag.example.com/search")
        self.assertEqual(cfg.method, "POST")
        self.assertEqual(cfg.auth_type, "bearer")
        self.assertEqual(cfg.auth_value, "token123")
        self.assertEqual(cfg.query_key, "question")
        self.assertEqual(cfg.response_path, "hits[].snippet")

    def test_from_knowledge_base_enabled_flag_and_defaults(self):
        """Test accept 'enabled' as alternate flag and apply defaults on missing/None values."""
        kb = {
            "enabled": True,
            "externalRag": {
                "url": "https://rag.example.com/api",
                "method": None,
                "authType": None,
                "queryKey": None,
                "responsePath": None,
            },
        }
        cfg = RagConfig.from_knowledge_base(kb)
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.method, "POST")
        self.assertEqual(cfg.query_key, "query")
        self.assertEqual(cfg.response_path, "results[].content")
        self.assertEqual(cfg.top_k, 3)
        self.assertEqual(cfg.grounding_mode, "balanced")
        self.assertEqual(cfg.auth_type, "none")

    def test_from_knowledge_base_tolerates_none_and_non_dict(self):
        """Test from_knowledge_base tolerates None and non-dict input."""
        cfg_none = RagConfig.from_knowledge_base(None)
        self.assertFalse(cfg_none.enabled)
        self.assertEqual(cfg_none.top_k, 3)

        cfg_str = RagConfig.from_knowledge_base("invalid")  # type: ignore
        self.assertFalse(cfg_str.enabled)
        self.assertEqual(cfg_str.top_k, 3)

from unittest.mock import patch
from services.rag_retriever_service import (
    RagConfig,
    Retriever,
    HttpRetriever,
    extract_path,
    load_knowledge_base_config,
    set_config_loader_hook,
)


class TestLoadKnowledgeBaseConfig(unittest.TestCase):
    def setUp(self):
        self._env_patch = patch.dict("os.environ", {"RAG_DEV_KB_FILE": ""})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        set_config_loader_hook(None)

    def test_path_a_saved_config_via_agent_id(self):
        """Path (a): Saved knowledgeBase loaded for agent_id with server-side secrets."""
        call_metadata = {"agent_id": "agent-007"}
        fake_saved_kb = {
            "ragEnabled": True,
            "topK": 5,
            "externalRag": {
                "url": "https://secure.vault.internal/rag",
                "authType": "bearer",
                "authValue": "super-secret-token",
            },
        }

        # Test via explicit config_loader parameter
        res = load_knowledge_base_config(
            call_metadata,
            config_loader=lambda aid: fake_saved_kb if aid == "agent-007" else None,
        )
        self.assertEqual(res, fake_saved_kb)
        self.assertEqual(res["externalRag"]["authValue"], "super-secret-token")

        # Test via set_config_loader_hook
        set_config_loader_hook(lambda aid: fake_saved_kb if aid == "agent-007" else None)
        res_hook = load_knowledge_base_config(call_metadata)
        self.assertEqual(res_hook, fake_saved_kb)

    def test_path_b_fallback_to_metadata_dev_only(self):
        """Path (b): When no saved config exists, fall back to call_metadata only when RAG_ALLOW_METADATA_CONFIG=true."""
        call_metadata = {
            "agent_id": "unconfigured-agent",
            "knowledgeBase": {
                "ragEnabled": True,
                "topK": 2,
                "externalRag": {"url": "https://api.dev/rag"},
            },
        }

        with patch.dict("os.environ", {"RAG_ALLOW_METADATA_CONFIG": "true"}):
            with self.assertLogs("rag-retriever-service", level="WARNING") as cm:
                res = load_knowledge_base_config(
                    call_metadata,
                    config_loader=lambda aid: None,
                )
            self.assertEqual(res, call_metadata["knowledgeBase"])
            self.assertTrue(any("Raw secrets must not travel in job metadata" in log for log in cm.output))

    def test_path_c_metadata_ignored_in_production(self):
        """Path (c): Metadata knowledgeBase is ignored and returns {} when RAG_ALLOW_METADATA_CONFIG is false/unset."""
        call_metadata = {
            "agent_id": "unconfigured-agent",
            "knowledgeBase": {
                "ragEnabled": True,
                "topK": 2,
            },
        }

        with patch.dict("os.environ", {"RAG_ALLOW_METADATA_CONFIG": "false"}):
            res = load_knowledge_base_config(
                call_metadata,
                config_loader=lambda aid: None,
            )
            self.assertEqual(res, {})

    def test_path_c_empty_or_invalid_metadata(self):
        """Path (c): Returns {} on empty or non-dict metadata."""
        self.assertEqual(load_knowledge_base_config(None), {})
        self.assertEqual(load_knowledge_base_config({}), {})
        self.assertEqual(load_knowledge_base_config("invalid"), {})  # type: ignore


if __name__ == "__main__":
    unittest.main()

