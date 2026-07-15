"""Tests for the non-executing camera-grounded reasoning preview."""

import unittest
from unittest.mock import Mock, patch

import server


TEST_IMAGE = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


class SceneReasoningPreviewTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_requires_camera_frame(self):
        response = self.client.post(
            "/api/scene-reasoning",
            json={"text": "walk toward the cup"},
        )
        self.assertEqual(response.status_code, 400)

    def test_text_fallback_is_preview_only(self):
        with (
            patch.object(server, "API_KEY", ""),
            patch.object(server.serial_mgr, "send") as send,
            patch.object(server, "append_interaction_log"),
        ):
            response = self.client.post(
                "/api/scene-reasoning",
                json={"text": "walk forward", "image": TEST_IMAGE},
            )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["preview_only"])
        self.assertEqual(data["commands"], [])
        self.assertEqual(data["proposed_commands"], ["kwkF"])
        self.assertEqual(data["reasoning"]["validation"]["status"], "scene_preview")
        send.assert_not_called()

    def test_vision_steps_are_filtered_and_never_executed(self):
        model_response = Mock()
        model_response.raise_for_status.return_value = None
        model_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": """{
                        "observations": [{
                            "label": "red cup",
                            "location": "upper left",
                            "confidence": "high",
                            "evidence": "red cylindrical object"
                        }],
                        "robot": {
                            "visible": true,
                            "summary": "yellow and black robot at lower center; heading uncertain",
                            "confidence": "medium"
                        },
                        "grounding": {"summary": "the red cup is the likely target"},
                        "uncertainties": ["robot heading is unclear"],
                        "proposed_steps": ["walk_forward", "firmware_write"],
                        "decision_summary": "A short forward move could approach the cup after heading verification.",
                        "verification": "Confirm the robot moved closer without contacting the box.",
                        "confidence": 0.72
                    }"""
                }
            }]
        }

        with (
            patch.object(server, "API_KEY", "test-key"),
            patch.object(server.requests, "post", return_value=model_response),
            patch.object(server.serial_mgr, "send") as send,
            patch.object(server, "append_interaction_log"),
        ):
            response = self.client.post(
                "/api/scene-reasoning",
                json={"text": "move toward the red cup", "image": TEST_IMAGE},
            )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["commands"], [])
        self.assertEqual(data["proposed_commands"], ["kwkF"])
        self.assertEqual(data["reasoning"]["scene"]["proposed_steps"], ["walk_forward"])
        self.assertEqual(data["reasoning"]["scene"]["observations"][0]["label"], "red cup")
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
