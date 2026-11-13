"""集中管理 E2E 选择器锚点。

生产代码只允许新增 data-testid / aria-label；这里只引用锚点，不写业务选择器。
"""

from __future__ import annotations


class S:
    # 布局 / 认证
    layout_root = "main-layout"
    login_root = "login-dialog"
    login_username = "login-username"
    login_password = "login-password"
    login_submit = "login-submit"
    login_error = "login-error"
    topbar_login = "topbar-login"
    user_menu = "user-menu"
    logout_button = "logout-button"
    open_settings = "open-settings"
    open_users = "open-users"

    # 左栏
    left_tab_files = "left-tab-files"
    left_tab_resources = "left-tab-resources"
    resource_root = "resource-browser"
    file_tree_root = "file-tree"
    file_preview_root = "file-preview"

    # 中栏
    chat_root = "chat-view"
    chat_messages = "chat-message-list"
    chat_input = "chat-input"
    chat_send = "chat-send"
    chat_stop = "chat-stop"
    data_preview_root = "data-preview"
    data_preview_title = "data-preview-title"
    data_preview_error = "data-preview-error"
    data_preview_files = "data-preview-files"
    data_preview_file_row = "data-preview-file-row"
    data_preview_image = "data-preview-image"
    data_preview_spec = "data-preview-spec"
    data_table_root = "data-table-preview"
    markdown_root = "markdown-preview"
    text_root = "text-preview"

    tool_detail_root = "tool-detail"
    tool_detail_name = "tool-detail-name"
    agent_detail_root = "agent-detail"
    agent_detail_name = "agent-detail-name"
    repository_root = "tool-repository"
    repository_count = "tool-repository-count"
    dataset_repository_root = "dataset-repository"
    settings_root = "settings-view"
    users_root = "users-view"
    cron_root = "cron-jobs-view"
    task_monitor_root = "task-monitor-view"

    @staticmethod
    def resource_section(resource_type: str) -> str:
        return f"resource-section-{resource_type}"

    @staticmethod
    def resource_count(resource_type: str) -> str:
        return f"resource-count-{resource_type}"

    @staticmethod
    def resource_row(resource_type: str, resource_id: str) -> str:
        return f"resource-row-{resource_type}-{resource_id}"

    @staticmethod
    def file_node(name: str) -> str:
        return f"file-node-{name}"

    @staticmethod
    def data_file_row(name: str) -> str:
        return f"data-file-row-{name}"

    @staticmethod
    def view_tab(view_id: str) -> str:
        return f'[data-view="{view_id}"]'
