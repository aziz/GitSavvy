import os

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from ..git_command import GitCommand
from ...common import util

TAG_DELETE_MESSAGE = "Tag deleted."
TAG_CREATE_PROMPT = "Enter tag:"
TAG_CREATE_MESSAGE_PROMPT = "Enter message:"
START_PUSH_MESSAGE = "Starting push..."
END_PUSH_MESSAGE = "Push complete."

VIEW_TITLE = "TAGS: {}"

LOCAL_TEMPLATE = """
  LOCAL:
{}
"""

REMOTE_TEMPLATE = """
  REMOTE ({}):
{}
"""

VIEW_HEADER_TEMPLATE = """
  BRANCH:  {branch_status}
  ROOT:    {repo_root}
  HEAD:    {current_head}
"""

NO_TAGS_MESSAGE = """
  Your repository has no tags.
"""

LOADING_TAGS_MESSAGE = """
  Please stand by while fetching tags from remote(s).
"""

KEY_BINDINGS_MENU = """
  #############
  ## ACTIONS ##
  #############

  [c] create
  [d] delete
  [p] push to remote
  [P] push all tags to remote
  [l] view commit

  ###########
  ## OTHER ##
  ###########

  [r] refresh status

-
"""

view_section_ranges = {}


class GsShowTagsCommand(WindowCommand, GitCommand):

    """
    Open a tags view for the active git repository.
    """

    def run(self):
        repo_path = self.repo_path
        title = VIEW_TITLE.format(os.path.basename(repo_path))
        tags_view = util.view.get_read_only_view(self, "tags")
        util.view.disable_other_plugins(tags_view)
        tags_view.set_name(title)
        tags_view.set_syntax_file("Packages/GitSavvy/syntax/tags.tmLanguage")
        tags_view.settings().set("git_savvy.repo_path", repo_path)
        tags_view.settings().set("word_wrap", False)
        self.window.focus_view(tags_view)
        tags_view.sel().clear()

        tags_view.run_command("gs_tags_refresh")


class GsTagsRefreshCommand(TextCommand, GitCommand):

    """
    Get the current state of the git repo and display tags and command
    menu to the user.
    """

    def run(self, edit, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(**kwargs))

    def run_async(self):
        view_contents = self.get_contents(loading=True)
        self.view.run_command("gs_replace_view_text", {"text": view_contents})
        sublime.set_timeout_async(lambda: self.append_tags())

    def get_contents(self, loading=False):
        """
        Build string to use as contents of tags view. Includes repository
        information in the header, per-tag information, and a key-bindings
        menu at the bottom.
        """
        header = VIEW_HEADER_TEMPLATE.format(
            branch_status=self.get_branch_status(),
            repo_root=self.repo_path,
            current_head=self.get_latest_commit_msg_for_head()
        )

        if loading:
            return header + LOADING_TAGS_MESSAGE + KEY_BINDINGS_MENU
        else:
            view_text = ""

            cursor = len(header)
            local, remotes = self.sort_tag_entries(self.get_tags())
            local_region, remote_region = (sublime.Region(0, 0), ) * 2

            def get_region(new_text):
                nonlocal cursor
                start = cursor
                cursor += len(new_text)
                end = cursor
                return sublime.Region(start, end)


            if local:
                local_lines = "\n".join(
                    "    {} {}".format(t.sha[:7], t.tag)
                    for t in local
                    )
                local_text = LOCAL_TEMPLATE.format(local_lines)
                local_region = get_region(local_text)
                view_text += local_text
            if remotes:
                for group in remotes:
                    remote_lines = "\n".join(
                        "    {} {}".format(t.sha[:7], t.tag)
                        for t in group.entries
                        )
                    remote_text = REMOTE_TEMPLATE.format(group.remote, remote_lines)
                    remote_region = get_region(remote_text)
                    view_text += remote_text

            view_text = view_text or NO_TAGS_MESSAGE

            contents = header + view_text + KEY_BINDINGS_MENU

            return contents, (local_region, remote_region)

    def append_tags(self):
        view_contents, ranges = self.get_contents()
        view_section_ranges[self.view.id()] = ranges
        self.view.run_command("gs_replace_view_text", {"text": view_contents})

    @staticmethod
    def sort_tag_entries(tag_list):
        """
        Take entries from `get_tags` and sort them into groups.
        """
        local, remotes = [], []

        for item in tag_list:
            if hasattr(item, "remote"):
                # TODO: remove entries that exist locally
                remotes.append(item)
            else:
                local.append(item)

        return local, remotes


class GsTagsFocusEventListener(EventListener):

    """
    If the current view is a tags view, refresh the view with
    the repository's tags when the view regains focus.
    """

    def on_activated(self, view):
        if view.settings().get("git_savvy.tags_view") == True:
            view.run_command("gs_tags_refresh")


class GsTagDeleteCommand(TextCommand, GitCommand):

    """
    Delete tag(s) in selection.
    """

    def run(self, edit):
        valid_ranges = view_section_ranges[self.view.id()][:3]

        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
            )

        items = tuple(line[4:].strip().split() for line in lines if line)

        if items:
            for item in items:
                self.git("tag", "-d", item[1])
            util.view.refresh_gitsavvy(self.view)
            sublime.status_message(TAG_DELETE_MESSAGE)


class GsTagCreateCommand(WindowCommand, GitCommand):

    """
    Through a series of panels, allow the user to add a tag and message.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        """
        Prompt the user for a tag name.
        """
        self.window.show_input_panel(
            TAG_CREATE_PROMPT,
            "",
            self.on_entered_tag,
            None,
            None
            )

    def on_entered_tag(self, tag_name):
        """
        After the user has entered a tag name, prompt the user for a
        tag message. If the message is empty, use the pre-defined one.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if not tag_name:
            return

        # TODO: do some validation

        self.tag_name = tag_name
        self.window.show_input_panel(
            TAG_CREATE_MESSAGE_PROMPT,
            "",
            self.on_entered_message,
            None,
            None
            )

    def on_entered_message(self, message):
        """
        Perform `git tag tag_name -F -`
        """
        # If the user pressed `esc` or otherwise cancelled
        if message == -1:
            return

        if not message:
            default_message = sublime.load_settings("GitSavvy.sublime-settings").get("default_tag_message")
            message = default_message.format(tag_name=self.tag_name)

        self.git("tag", self.tag_name, "-F", "-", stdin=message)


class GsTagPushCommand(TextCommand, GitCommand):

    """
    Displays a panel of all remotes defined for the repository, then push
    selected or all tag(s) to the selected remote.
    """

    def run(self, edit, push_all=False):
        if not push_all:
            valid_ranges = view_section_ranges[self.view.id()][:3]

            lines = util.view.get_lines_from_regions(
                self.view,
                self.view.sel(),
                valid_ranges=valid_ranges
                )

            self.items = tuple(line[4:].strip().split() for line in lines if line)

        self.push_all = push_all
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        """
        Display a panel of all remotes defined for the repo, then proceed to
        `on_select_remote`. If no remotes are defined, notify the user and
        proceed no further.
        """
        self.remotes = list(self.get_remotes().keys())

        if not self.remotes:
            self.view.window().show_quick_panel([NO_REMOTES_MESSAGE], None)
        else:
            self.view.window().show_quick_panel(
                self.remotes,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT
                )

    def on_select_remote(self, remote_index):
        """
        Push tag(s) to the remote that was previously selected
        """

        #if the user pressed `esc` or otherwise cancelled
        if remote_index == -1:
            return

        selected_remote = self.remotes[remote_index]

        sublime.status_message(START_PUSH_MESSAGE)
        if self.push_all:
            self.git("push", selected_remote, "--tags")
        elif hasattr(self, "items") and self.items:
            refs = ""
            for item in self.items:
                refs += "refs/tags/" + item[1] + ":"
            refs = refs[:-1]

            self.git("push", selected_remote, refs)

        sublime.status_message(END_PUSH_MESSAGE)


class GsTagViewLogCommand(TextCommand, GitCommand):

    """
    Display a panel containing the commit log for the selected tag's hash.
    """

    def run(self, edit):
        valid_ranges = view_section_ranges[self.view.id()][:3]

        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
            )

        items = tuple(line[4:].strip().split() for line in lines if line)

        if items:
            for item in items:
                self.git("log", "-1", "--pretty=medium", item[0], show_panel=True)
                break
