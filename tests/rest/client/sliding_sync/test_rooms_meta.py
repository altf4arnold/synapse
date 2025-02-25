#
# This file is licensed under the Affero General Public License (AGPL) version 3.
#
# Copyright (C) 2024 New Vector, Ltd
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# See the GNU Affero General Public License for more details:
# <https://www.gnu.org/licenses/agpl-3.0.html>.
#
import logging

from parameterized import parameterized_class

from twisted.test.proto_helpers import MemoryReactor

import synapse.rest.admin
from synapse.api.constants import EventContentFields, EventTypes, Membership
from synapse.api.room_versions import RoomVersions
from synapse.rest.client import login, room, sync
from synapse.server import HomeServer
from synapse.util import Clock

from tests.rest.client.sliding_sync.test_sliding_sync import SlidingSyncBase
from tests.test_utils.event_injection import create_event

logger = logging.getLogger(__name__)


# FIXME: This can be removed once we bump `SCHEMA_COMPAT_VERSION` and run the
# foreground update for
# `sliding_sync_joined_rooms`/`sliding_sync_membership_snapshots` (tracked by
# https://github.com/element-hq/synapse/issues/17623)
@parameterized_class(
    ("use_new_tables",),
    [
        (True,),
        (False,),
    ],
    class_name_func=lambda cls,
    num,
    params_dict: f"{cls.__name__}_{'new' if params_dict['use_new_tables'] else 'fallback'}",
)
class SlidingSyncRoomsMetaTestCase(SlidingSyncBase):
    """
    Test rooms meta info like name, avatar, joined_count, invited_count, is_dm,
    bump_stamp in the Sliding Sync API.
    """

    servlets = [
        synapse.rest.admin.register_servlets,
        login.register_servlets,
        room.register_servlets,
        sync.register_servlets,
    ]

    def prepare(self, reactor: MemoryReactor, clock: Clock, hs: HomeServer) -> None:
        self.store = hs.get_datastores().main
        self.storage_controllers = hs.get_storage_controllers()
        self.state_handler = self.hs.get_state_handler()
        persistence = self.hs.get_storage_controllers().persistence
        assert persistence is not None
        self.persistence = persistence

        super().prepare(reactor, clock, hs)

    def test_rooms_meta_when_joined(self) -> None:
        """
        Test that the `rooms` `name` and `avatar` are included in the response and
        reflect the current state of the room when the user is joined to the room.
        """
        user1_id = self.register_user("user1", "pass")
        user1_tok = self.login(user1_id, "pass")
        user2_id = self.register_user("user2", "pass")
        user2_tok = self.login(user2_id, "pass")

        room_id1 = self.helper.create_room_as(
            user2_id,
            tok=user2_tok,
            extra_content={
                "name": "my super room",
            },
        )
        # Set the room avatar URL
        self.helper.send_state(
            room_id1,
            EventTypes.RoomAvatar,
            {"url": "mxc://DUMMY_MEDIA_ID"},
            tok=user2_tok,
        )

        self.helper.join(room_id1, user1_id, tok=user1_tok)

        # Make the Sliding Sync request
        sync_body = {
            "lists": {
                "foo-list": {
                    "ranges": [[0, 1]],
                    "required_state": [],
                    "timeline_limit": 0,
                }
            }
        }
        response_body, _ = self.do_sync(sync_body, tok=user1_tok)

        # Reflect the current state of the room
        self.assertEqual(
            response_body["rooms"][room_id1]["name"],
            "my super room",
            response_body["rooms"][room_id1],
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["avatar"],
            "mxc://DUMMY_MEDIA_ID",
            response_body["rooms"][room_id1],
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["joined_count"],
            2,
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["invited_count"],
            0,
        )
        self.assertIsNone(
            response_body["rooms"][room_id1].get("is_dm"),
        )

    def test_rooms_meta_when_invited(self) -> None:
        """
        Test that the `rooms` `name` and `avatar` are included in the response and
        reflect the current state of the room when the user is invited to the room.
        """
        user1_id = self.register_user("user1", "pass")
        user1_tok = self.login(user1_id, "pass")
        user2_id = self.register_user("user2", "pass")
        user2_tok = self.login(user2_id, "pass")

        room_id1 = self.helper.create_room_as(
            user2_id,
            tok=user2_tok,
            extra_content={
                "name": "my super room",
            },
        )
        # Set the room avatar URL
        self.helper.send_state(
            room_id1,
            EventTypes.RoomAvatar,
            {"url": "mxc://DUMMY_MEDIA_ID"},
            tok=user2_tok,
        )

        # User1 is invited to the room
        self.helper.invite(room_id1, src=user2_id, targ=user1_id, tok=user2_tok)

        # Update the room name after user1 has left
        self.helper.send_state(
            room_id1,
            EventTypes.Name,
            {"name": "my super duper room"},
            tok=user2_tok,
        )
        # Update the room avatar URL after user1 has left
        self.helper.send_state(
            room_id1,
            EventTypes.RoomAvatar,
            {"url": "mxc://UPDATED_DUMMY_MEDIA_ID"},
            tok=user2_tok,
        )

        # Make the Sliding Sync request
        sync_body = {
            "lists": {
                "foo-list": {
                    "ranges": [[0, 1]],
                    "required_state": [],
                    "timeline_limit": 0,
                }
            }
        }
        response_body, _ = self.do_sync(sync_body, tok=user1_tok)

        # This should still reflect the current state of the room even when the user is
        # invited.
        self.assertEqual(
            response_body["rooms"][room_id1]["name"],
            "my super duper room",
            response_body["rooms"][room_id1],
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["avatar"],
            "mxc://UPDATED_DUMMY_MEDIA_ID",
            response_body["rooms"][room_id1],
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["joined_count"],
            1,
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["invited_count"],
            1,
        )
        self.assertIsNone(
            response_body["rooms"][room_id1].get("is_dm"),
        )

    def test_rooms_meta_when_banned(self) -> None:
        """
        Test that the `rooms` `name` and `avatar` reflect the state of the room when the
        user was banned (do not leak current state).
        """
        user1_id = self.register_user("user1", "pass")
        user1_tok = self.login(user1_id, "pass")
        user2_id = self.register_user("user2", "pass")
        user2_tok = self.login(user2_id, "pass")

        room_id1 = self.helper.create_room_as(
            user2_id,
            tok=user2_tok,
            extra_content={
                "name": "my super room",
            },
        )
        # Set the room avatar URL
        self.helper.send_state(
            room_id1,
            EventTypes.RoomAvatar,
            {"url": "mxc://DUMMY_MEDIA_ID"},
            tok=user2_tok,
        )

        self.helper.join(room_id1, user1_id, tok=user1_tok)
        self.helper.ban(room_id1, src=user2_id, targ=user1_id, tok=user2_tok)

        # Update the room name after user1 has left
        self.helper.send_state(
            room_id1,
            EventTypes.Name,
            {"name": "my super duper room"},
            tok=user2_tok,
        )
        # Update the room avatar URL after user1 has left
        self.helper.send_state(
            room_id1,
            EventTypes.RoomAvatar,
            {"url": "mxc://UPDATED_DUMMY_MEDIA_ID"},
            tok=user2_tok,
        )

        # Make the Sliding Sync request
        sync_body = {
            "lists": {
                "foo-list": {
                    "ranges": [[0, 1]],
                    "required_state": [],
                    "timeline_limit": 0,
                }
            }
        }
        response_body, _ = self.do_sync(sync_body, tok=user1_tok)

        # Reflect the state of the room at the time of leaving
        self.assertEqual(
            response_body["rooms"][room_id1]["name"],
            "my super room",
            response_body["rooms"][room_id1],
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["avatar"],
            "mxc://DUMMY_MEDIA_ID",
            response_body["rooms"][room_id1],
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["joined_count"],
            # FIXME: The actual number should be "1" (user2) but we currently don't
            # support this for rooms where the user has left/been banned.
            0,
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["invited_count"],
            0,
        )
        self.assertIsNone(
            response_body["rooms"][room_id1].get("is_dm"),
        )

    def test_rooms_meta_heroes(self) -> None:
        """
        Test that the `rooms` `heroes` are included in the response when the room
        doesn't have a room name set.
        """
        user1_id = self.register_user("user1", "pass")
        user1_tok = self.login(user1_id, "pass")
        user2_id = self.register_user("user2", "pass")
        user2_tok = self.login(user2_id, "pass")
        user3_id = self.register_user("user3", "pass")
        _user3_tok = self.login(user3_id, "pass")

        room_id1 = self.helper.create_room_as(
            user2_id,
            tok=user2_tok,
            extra_content={
                "name": "my super room",
            },
        )
        self.helper.join(room_id1, user1_id, tok=user1_tok)
        # User3 is invited
        self.helper.invite(room_id1, src=user2_id, targ=user3_id, tok=user2_tok)

        room_id2 = self.helper.create_room_as(
            user2_id,
            tok=user2_tok,
            extra_content={
                # No room name set so that `heroes` is populated
                #
                # "name": "my super room2",
            },
        )
        self.helper.join(room_id2, user1_id, tok=user1_tok)
        # User3 is invited
        self.helper.invite(room_id2, src=user2_id, targ=user3_id, tok=user2_tok)

        # Make the Sliding Sync request
        sync_body = {
            "lists": {
                "foo-list": {
                    "ranges": [[0, 1]],
                    "required_state": [],
                    "timeline_limit": 0,
                }
            }
        }
        response_body, _ = self.do_sync(sync_body, tok=user1_tok)

        # Room1 has a name so we shouldn't see any `heroes` which the client would use
        # the calculate the room name themselves.
        self.assertEqual(
            response_body["rooms"][room_id1]["name"],
            "my super room",
            response_body["rooms"][room_id1],
        )
        self.assertIsNone(response_body["rooms"][room_id1].get("heroes"))
        self.assertEqual(
            response_body["rooms"][room_id1]["joined_count"],
            2,
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["invited_count"],
            1,
        )

        # Room2 doesn't have a name so we should see `heroes` populated
        self.assertIsNone(response_body["rooms"][room_id2].get("name"))
        self.assertCountEqual(
            [
                hero["user_id"]
                for hero in response_body["rooms"][room_id2].get("heroes", [])
            ],
            # Heroes shouldn't include the user themselves (we shouldn't see user1)
            [user2_id, user3_id],
        )
        self.assertEqual(
            response_body["rooms"][room_id2]["joined_count"],
            2,
        )
        self.assertEqual(
            response_body["rooms"][room_id2]["invited_count"],
            1,
        )

        # We didn't request any state so we shouldn't see any `required_state`
        self.assertIsNone(response_body["rooms"][room_id1].get("required_state"))
        self.assertIsNone(response_body["rooms"][room_id2].get("required_state"))

    def test_rooms_meta_heroes_max(self) -> None:
        """
        Test that the `rooms` `heroes` only includes the first 5 users (not including
        yourself).
        """
        user1_id = self.register_user("user1", "pass")
        user1_tok = self.login(user1_id, "pass")
        user2_id = self.register_user("user2", "pass")
        user2_tok = self.login(user2_id, "pass")
        user3_id = self.register_user("user3", "pass")
        user3_tok = self.login(user3_id, "pass")
        user4_id = self.register_user("user4", "pass")
        user4_tok = self.login(user4_id, "pass")
        user5_id = self.register_user("user5", "pass")
        user5_tok = self.login(user5_id, "pass")
        user6_id = self.register_user("user6", "pass")
        user6_tok = self.login(user6_id, "pass")
        user7_id = self.register_user("user7", "pass")
        user7_tok = self.login(user7_id, "pass")

        room_id1 = self.helper.create_room_as(
            user2_id,
            tok=user2_tok,
            extra_content={
                # No room name set so that `heroes` is populated
                #
                # "name": "my super room",
            },
        )
        self.helper.join(room_id1, user1_id, tok=user1_tok)
        self.helper.join(room_id1, user3_id, tok=user3_tok)
        self.helper.join(room_id1, user4_id, tok=user4_tok)
        self.helper.join(room_id1, user5_id, tok=user5_tok)
        self.helper.join(room_id1, user6_id, tok=user6_tok)
        self.helper.join(room_id1, user7_id, tok=user7_tok)

        # Make the Sliding Sync request
        sync_body = {
            "lists": {
                "foo-list": {
                    "ranges": [[0, 1]],
                    "required_state": [],
                    "timeline_limit": 0,
                }
            }
        }
        response_body, _ = self.do_sync(sync_body, tok=user1_tok)

        # Room2 doesn't have a name so we should see `heroes` populated
        self.assertIsNone(response_body["rooms"][room_id1].get("name"))
        self.assertCountEqual(
            [
                hero["user_id"]
                for hero in response_body["rooms"][room_id1].get("heroes", [])
            ],
            # Heroes should be the first 5 users in the room (excluding the user
            # themselves, we shouldn't see `user1`)
            [user2_id, user3_id, user4_id, user5_id, user6_id],
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["joined_count"],
            7,
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["invited_count"],
            0,
        )

        # We didn't request any state so we shouldn't see any `required_state`
        self.assertIsNone(response_body["rooms"][room_id1].get("required_state"))

    def test_rooms_meta_heroes_when_banned(self) -> None:
        """
        Test that the `rooms` `heroes` are included in the response when the room
        doesn't have a room name set but doesn't leak information past their ban.
        """
        user1_id = self.register_user("user1", "pass")
        user1_tok = self.login(user1_id, "pass")
        user2_id = self.register_user("user2", "pass")
        user2_tok = self.login(user2_id, "pass")
        user3_id = self.register_user("user3", "pass")
        _user3_tok = self.login(user3_id, "pass")
        user4_id = self.register_user("user4", "pass")
        user4_tok = self.login(user4_id, "pass")
        user5_id = self.register_user("user5", "pass")
        _user5_tok = self.login(user5_id, "pass")

        room_id1 = self.helper.create_room_as(
            user2_id,
            tok=user2_tok,
            extra_content={
                # No room name set so that `heroes` is populated
                #
                # "name": "my super room",
            },
        )
        # User1 joins the room
        self.helper.join(room_id1, user1_id, tok=user1_tok)
        # User3 is invited
        self.helper.invite(room_id1, src=user2_id, targ=user3_id, tok=user2_tok)

        # User1 is banned from the room
        self.helper.ban(room_id1, src=user2_id, targ=user1_id, tok=user2_tok)

        # User4 joins the room after user1 is banned
        self.helper.join(room_id1, user4_id, tok=user4_tok)
        # User5 is invited after user1 is banned
        self.helper.invite(room_id1, src=user2_id, targ=user5_id, tok=user2_tok)

        # Make the Sliding Sync request
        sync_body = {
            "lists": {
                "foo-list": {
                    "ranges": [[0, 1]],
                    "required_state": [],
                    "timeline_limit": 0,
                }
            }
        }
        response_body, _ = self.do_sync(sync_body, tok=user1_tok)

        # Room2 doesn't have a name so we should see `heroes` populated
        self.assertIsNone(response_body["rooms"][room_id1].get("name"))
        self.assertCountEqual(
            [
                hero["user_id"]
                for hero in response_body["rooms"][room_id1].get("heroes", [])
            ],
            # Heroes shouldn't include the user themselves (we shouldn't see user1). We
            # also shouldn't see user4 since they joined after user1 was banned.
            #
            # FIXME: The actual result should be `[user2_id, user3_id]` but we currently
            # don't support this for rooms where the user has left/been banned.
            [],
        )

        self.assertEqual(
            response_body["rooms"][room_id1]["joined_count"],
            # FIXME: The actual number should be "1" (user2) but we currently don't
            # support this for rooms where the user has left/been banned.
            0,
        )
        self.assertEqual(
            response_body["rooms"][room_id1]["invited_count"],
            # We shouldn't see user5 since they were invited after user1 was banned.
            #
            # FIXME: The actual number should be "1" (user3) but we currently don't
            # support this for rooms where the user has left/been banned.
            0,
        )

    def test_rooms_bump_stamp(self) -> None:
        """
        Test that `bump_stamp` is present and pointing to relevant events.
        """
        user1_id = self.register_user("user1", "pass")
        user1_tok = self.login(user1_id, "pass")

        room_id1 = self.helper.create_room_as(
            user1_id,
            tok=user1_tok,
        )
        event_response1 = message_response = self.helper.send(
            room_id1, "message in room1", tok=user1_tok
        )
        event_pos1 = self.get_success(
            self.store.get_position_for_event(event_response1["event_id"])
        )
        room_id2 = self.helper.create_room_as(
            user1_id,
            tok=user1_tok,
        )
        send_response2 = self.helper.send(room_id2, "message in room2", tok=user1_tok)
        event_pos2 = self.get_success(
            self.store.get_position_for_event(send_response2["event_id"])
        )

        # Send a reaction in room1 but it shouldn't affect the `bump_stamp`
        # because reactions are not part of the `DEFAULT_BUMP_EVENT_TYPES`
        self.helper.send_event(
            room_id1,
            type=EventTypes.Reaction,
            content={
                "m.relates_to": {
                    "event_id": message_response["event_id"],
                    "key": "👍",
                    "rel_type": "m.annotation",
                }
            },
            tok=user1_tok,
        )

        # Make the Sliding Sync request
        sync_body = {
            "lists": {
                "foo-list": {
                    "ranges": [[0, 1]],
                    "required_state": [],
                    "timeline_limit": 100,
                }
            }
        }
        response_body, _ = self.do_sync(sync_body, tok=user1_tok)

        # Make sure it has the foo-list we requested
        self.assertListEqual(
            list(response_body["lists"].keys()),
            ["foo-list"],
            response_body["lists"].keys(),
        )

        # Make sure the list includes the rooms in the right order
        self.assertEqual(
            len(response_body["lists"]["foo-list"]["ops"]),
            1,
            response_body["lists"]["foo-list"],
        )
        op = response_body["lists"]["foo-list"]["ops"][0]
        self.assertEqual(op["op"], "SYNC")
        self.assertEqual(op["range"], [0, 1])
        # Note that we don't order the ops anymore, so we need to compare sets.
        self.assertIncludes(set(op["room_ids"]), {room_id1, room_id2}, exact=True)

        # The `bump_stamp` for room1 should point at the latest message (not the
        # reaction since it's not one of the `DEFAULT_BUMP_EVENT_TYPES`)
        self.assertEqual(
            response_body["rooms"][room_id1]["bump_stamp"],
            event_pos1.stream,
            response_body["rooms"][room_id1],
        )

        # The `bump_stamp` for room2 should point at the latest message
        self.assertEqual(
            response_body["rooms"][room_id2]["bump_stamp"],
            event_pos2.stream,
            response_body["rooms"][room_id2],
        )

    def test_rooms_bump_stamp_backfill(self) -> None:
        """
        Test that `bump_stamp` ignores backfilled events, i.e. events with a
        negative stream ordering.
        """
        user1_id = self.register_user("user1", "pass")
        user1_tok = self.login(user1_id, "pass")

        # Create a remote room
        creator = "@user:other"
        room_id = "!foo:other"
        room_version = RoomVersions.V10
        shared_kwargs = {
            "room_id": room_id,
            "room_version": room_version.identifier,
        }

        create_tuple = self.get_success(
            create_event(
                self.hs,
                prev_event_ids=[],
                type=EventTypes.Create,
                state_key="",
                content={
                    # The `ROOM_CREATOR` field could be removed if we used a room
                    # version > 10 (in favor of relying on `sender`)
                    EventContentFields.ROOM_CREATOR: creator,
                    EventContentFields.ROOM_VERSION: room_version.identifier,
                },
                sender=creator,
                **shared_kwargs,
            )
        )
        creator_tuple = self.get_success(
            create_event(
                self.hs,
                prev_event_ids=[create_tuple[0].event_id],
                auth_event_ids=[create_tuple[0].event_id],
                type=EventTypes.Member,
                state_key=creator,
                content={"membership": Membership.JOIN},
                sender=creator,
                **shared_kwargs,
            )
        )
        # We add a message event as a valid "bump type"
        msg_tuple = self.get_success(
            create_event(
                self.hs,
                prev_event_ids=[creator_tuple[0].event_id],
                auth_event_ids=[create_tuple[0].event_id],
                type=EventTypes.Message,
                content={"body": "foo", "msgtype": "m.text"},
                sender=creator,
                **shared_kwargs,
            )
        )
        invite_tuple = self.get_success(
            create_event(
                self.hs,
                prev_event_ids=[msg_tuple[0].event_id],
                auth_event_ids=[create_tuple[0].event_id, creator_tuple[0].event_id],
                type=EventTypes.Member,
                state_key=user1_id,
                content={"membership": Membership.INVITE},
                sender=creator,
                **shared_kwargs,
            )
        )

        remote_events_and_contexts = [
            create_tuple,
            creator_tuple,
            msg_tuple,
            invite_tuple,
        ]

        # Ensure the local HS knows the room version
        self.get_success(self.store.store_room(room_id, creator, False, room_version))

        # Persist these events as backfilled events.
        for event, context in remote_events_and_contexts:
            self.get_success(
                self.persistence.persist_event(event, context, backfilled=True)
            )

        # Now we join the local user to the room. We want to make this feel as close to
        # the real `process_remote_join()` as possible but we'd like to avoid some of
        # the auth checks that would be done in the real code.
        #
        # FIXME: The test was originally written using this less-real
        # `persist_event(...)` shortcut but it would be nice to use the real remote join
        # process in a `FederatingHomeserverTestCase`.
        flawed_join_tuple = self.get_success(
            create_event(
                self.hs,
                prev_event_ids=[invite_tuple[0].event_id],
                # This doesn't work correctly to create an `EventContext` that includes
                # both of these state events. I assume it's because we're working on our
                # local homeserver which has the remote state set as `outlier`. We have
                # to create our own EventContext below to get this right.
                auth_event_ids=[create_tuple[0].event_id, invite_tuple[0].event_id],
                type=EventTypes.Member,
                state_key=user1_id,
                content={"membership": Membership.JOIN},
                sender=user1_id,
                **shared_kwargs,
            )
        )
        # We have to create our own context to get the state set correctly. If we use
        # the `EventContext` from the `flawed_join_tuple`, the `current_state_events`
        # table will only have the join event in it which should never happen in our
        # real server.
        join_event = flawed_join_tuple[0]
        join_context = self.get_success(
            self.state_handler.compute_event_context(
                join_event,
                state_ids_before_event={
                    (e.type, e.state_key): e.event_id
                    for e in [create_tuple[0], invite_tuple[0]]
                },
                partial_state=False,
            )
        )
        self.get_success(self.persistence.persist_event(join_event, join_context))

        # Doing an SS request should return a positive `bump_stamp`, even though
        # the only event that matches the bump types has as negative stream
        # ordering.
        sync_body = {
            "lists": {
                "foo-list": {
                    "ranges": [[0, 1]],
                    "required_state": [],
                    "timeline_limit": 5,
                }
            }
        }
        response_body, _ = self.do_sync(sync_body, tok=user1_tok)

        self.assertGreater(response_body["rooms"][room_id]["bump_stamp"], 0)

    def test_rooms_bump_stamp_invites(self) -> None:
        """
        Test that `bump_stamp` is present and points to the membership event,
        and not later events, for non-joined rooms
        """

        user1_id = self.register_user("user1", "pass")
        user1_tok = self.login(user1_id, "pass")

        user2_id = self.register_user("user2", "pass")
        user2_tok = self.login(user2_id, "pass")

        room_id = self.helper.create_room_as(
            user2_id,
            tok=user2_tok,
        )

        # Invite user1 to the room
        invite_response = self.helper.invite(room_id, user2_id, user1_id, tok=user2_tok)

        # More messages happen after the invite
        self.helper.send(room_id, "message in room1", tok=user2_tok)

        # We expect the bump_stamp to match the invite.
        invite_pos = self.get_success(
            self.store.get_position_for_event(invite_response["event_id"])
        )

        # Doing an SS request should return a `bump_stamp` of the invite event,
        # rather than the message that was sent after.
        sync_body = {
            "lists": {
                "foo-list": {
                    "ranges": [[0, 1]],
                    "required_state": [],
                    "timeline_limit": 5,
                }
            }
        }
        response_body, _ = self.do_sync(sync_body, tok=user1_tok)

        self.assertEqual(
            response_body["rooms"][room_id]["bump_stamp"], invite_pos.stream
        )
