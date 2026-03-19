"""Slack listener — connects to Slack via Socket Mode using slack-bolt.

Re-exports the main SlackListener class for convenience.
"""

from listeners.slack.listener import SlackListener

__all__ = ["SlackListener"]
