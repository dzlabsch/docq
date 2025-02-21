"""Functions to manage space groups."""

import logging as log
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import List, Tuple

from .support.store import get_sqlite_system_file

SQL_CREATE_SPACE_GROUPS_TABLE = """
CREATE TABLE IF NOT EXISTS space_groups (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL,
    name TEXT UNIQUE,
    summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

SQL_CREATE_SPACE_GROUP_MEMBERS_TABLE = """
CREATE TABLE IF NOT EXISTS space_group_members (
    group_id INTEGER NOT NULL,
    space_id INTEGER NOT NULL,
    FOREIGN KEY (group_id) REFERENCES space_groups (id) ON DELETE CASCADE,
    FOREIGN KEY (space_id) REFERENCES spaces (id),
    PRIMARY KEY (group_id, space_id)
)
"""


def _init() -> None:
    """Initialize the database."""
    with closing(
        sqlite3.connect(get_sqlite_system_file(), detect_types=sqlite3.PARSE_DECLTYPES)
    ) as connection, closing(connection.cursor()) as cursor:
        cursor.execute(SQL_CREATE_SPACE_GROUPS_TABLE)
        cursor.execute(SQL_CREATE_SPACE_GROUP_MEMBERS_TABLE)
        connection.commit()


def list_space_groups(
    org_id: int, name_match: str = None
) -> List[Tuple[int, int, str, List[Tuple[int, str]], datetime, datetime]]:
    """List space groups.

    Args:
        org_id (int): The org id.
        name_match (str, optional): The space group name match. Defaults to None.

    Returns:
        List[Tuple[int, str, List[Tuple[int, str]], datetime, datetime]]: The list of space groups.
    """
    log.debug("Listing space groups: %s", name_match)
    with closing(
        sqlite3.connect(get_sqlite_system_file(), detect_types=sqlite3.PARSE_DECLTYPES)
    ) as connection, closing(connection.cursor()) as cursor:
        space_groups = cursor.execute(
            "SELECT id, org_id, name, summary, created_at, updated_at FROM space_groups WHERE org_id = ? AND name LIKE ?",
            (
                org_id,
                f"%{name_match}%" if name_match else "%",
            ),
        ).fetchall()

        members = cursor.execute(
            "SELECT c.group_id, s.id, s.name from spaces s, space_group_members c WHERE c.group_id in ({}) AND c.space_id = s.id".format(  # noqa: S608
                ",".join([str(x[0]) for x in space_groups])
            )
        ).fetchall()

        return [
            (x[0], x[1], x[2], x[3], [(y[1], y[2]) for y in members if y[0] == x[0]], x[4], x[5]) for x in space_groups
        ]


def create_space_group(org_id: int, name: str, summary: str = None) -> bool:
    """Create a space group.

    Args:
        org_id (int): The org id.
        name (str): The space group name.
        summary (str, optional): The space group summary. Defaults to None.

    Returns:
        bool: True if the space group is created, False otherwise.
    """
    log.debug("Creating space group: %s", name)
    with closing(
        sqlite3.connect(get_sqlite_system_file(), detect_types=sqlite3.PARSE_DECLTYPES)
    ) as connection, closing(connection.cursor()) as cursor:
        cursor.execute(
            "INSERT INTO space_groups (org_id, name, summary) VALUES (?, ?, ?)",
            (
                org_id,
                name,
                summary,
            ),
        )
        connection.commit()
        return True


def update_space_group(id_: int, org_id: int, members: List[int], name: str = None, summary: str = None) -> bool:
    """Update a group.

    Args:
        id_ (int): The group id.
        org_id (int): The org id.
        members (List[int]): The list of space ids.
        name (str, optional): The group name. Defaults to None.
        summary (str, optional): The group summary. Defaults to None.

    Returns:
        bool: True if the group is updated, False otherwise.
    """
    log.debug("Updating space group: %d", id_)

    query = "UPDATE space_groups SET updated_at = ?"
    params = [
        datetime.now(),
    ]

    if name:
        query += ", name = ?"
        params.append(name)

    if summary:
        query += ", summary = ?"
        params.append(summary)

    query += " WHERE id = ? AND org_id = ?"
    params.append(id_)
    params.append(org_id)

    with closing(
        sqlite3.connect(get_sqlite_system_file(), detect_types=sqlite3.PARSE_DECLTYPES)
    ) as connection, closing(connection.cursor()) as cursor:
        cursor.execute(query, params)
        cursor.execute("DELETE FROM space_group_members WHERE group_id = ?", (id_,))
        cursor.executemany(
            "INSERT INTO space_group_members (group_id, space_id) VALUES (?, ?)", [(id_, x) for x in members]
        )
        connection.commit()
        return True


def delete_space_group(id_: int, org_id: int) -> bool:
    """Delete a space group.

    Args:
        id_ (int): The space group id.
        org_id (int): The org id.

    Returns:
        bool: True if the space group is deleted, False otherwise.
    """
    log.debug("Deleting group: %d", id_)
    with closing(
        sqlite3.connect(get_sqlite_system_file(), detect_types=sqlite3.PARSE_DECLTYPES)
    ) as connection, closing(connection.cursor()) as cursor:
        cursor.execute("DELETE FROM space_group_members WHERE group_id = ?", (id_,))
        cursor.execute("DELETE FROM space_groups WHERE id = ? AND org_id = ?", (id_, org_id))
        connection.commit()
        return True
