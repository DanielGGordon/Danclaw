"""Slack listener — connects to Slack via Socket Mode using slack-bolt.

Re-exports the main SlackListener and SlackFanoutPoster classes for convenience.
"""

from listeners.slack.listener import SlackFanoutPoster, SlackListener

__all__ = ["SlackFanoutPoster", "SlackListener"]
