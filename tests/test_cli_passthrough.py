import unittest

from cli_passthrough import (
    is_dangerous_custom_command,
    parse_cli_args,
    prepare_custom_args,
    validate_custom_args,
)


class CliPassthroughTests(unittest.TestCase):
    def test_parse_cli_args(self) -> None:
        args = parse_cli_args("-p 'hello world' --model gemini-2.5-pro")
        self.assertEqual(args, ["-p", "hello world", "--model", "gemini-2.5-pro"])

        with self.assertRaises(ValueError):
            parse_cli_args("-p 'unclosed string")

    def test_validate_custom_args_rules(self) -> None:
        # Reject -i / --prompt-interactive (exact and with value assignment)
        v1, err1 = validate_custom_args(["-i"])
        self.assertFalse(v1)
        self.assertIn("禁止使用互動模式", err1)

        v2, err2 = validate_custom_args(["-p", "test", "--prompt-interactive"])
        self.assertFalse(v2)
        self.assertIn("禁止使用互動模式", err2)

        v2b, err2b = validate_custom_args(["-p", "test", "--prompt-interactive=true"])
        self.assertFalse(v2b)
        self.assertIn("禁止使用互動模式", err2b)

        v2c, err2c = validate_custom_args(["-p", "test", "-i=1"])
        self.assertFalse(v2c)
        self.assertIn("禁止使用互動模式", err2c)

        # Allow safe subcommands
        self.assertTrue(validate_custom_args(["models"])[0])
        self.assertTrue(validate_custom_args(["agents"])[0])
        self.assertTrue(validate_custom_args(["changelog"])[0])
        self.assertTrue(validate_custom_args(["--help"])[0])
        self.assertTrue(validate_custom_args(["--version"])[0])

        # Require --print / -p if not a safe subcommand
        v3, err3 = validate_custom_args(["--model", "gemini-2.5-pro"])
        self.assertFalse(v3)
        self.assertIn("必須包含", err3)

        v4, _ = validate_custom_args(["-p", "hello", "--model", "gemini-2.5-pro"])
        self.assertTrue(v4)

    def test_dangerous_detection(self) -> None:
        # Trigger 1: --dangerously-skip-permissions
        self.assertTrue(is_dangerous_custom_command(["-p", "run", "--dangerously-skip-permissions"]))
        self.assertTrue(is_dangerous_custom_command(["-p", "run", "--dangerously-skip-permissions=true"]))

        # Trigger 2: update or install
        self.assertTrue(is_dangerous_custom_command(["update"]))
        self.assertTrue(is_dangerous_custom_command(["install"]))

        # Trigger 3: plugin install/uninstall/enable/disable/import/link
        self.assertTrue(is_dangerous_custom_command(["plugin", "install", "my-plugin"]))
        self.assertTrue(is_dangerous_custom_command(["plugin", "uninstall", "my-plugin"]))
        self.assertTrue(is_dangerous_custom_command(["plugins", "enable", "my-plugin"]))
        self.assertTrue(is_dangerous_custom_command(["plugin", "disable", "my-plugin"]))
        self.assertTrue(is_dangerous_custom_command(["plugin", "import", "my-plugin"]))
        self.assertTrue(is_dangerous_custom_command(["plugin", "link", "my-plugin"]))

        # Safe plugin commands
        self.assertFalse(is_dangerous_custom_command(["plugin", "list"]))
        self.assertFalse(is_dangerous_custom_command(["models"]))
        self.assertFalse(is_dangerous_custom_command(["-p", "test"]))

    def test_prepare_custom_args(self) -> None:
        res = prepare_custom_args(["-p", "test"], enforce_sandbox=True)
        self.assertIn("--sandbox", res)

        res2 = prepare_custom_args(["-p", "test", "--sandbox"], enforce_sandbox=True)
        self.assertEqual(res2.count("--sandbox"), 1)


if __name__ == "__main__":
    unittest.main()
