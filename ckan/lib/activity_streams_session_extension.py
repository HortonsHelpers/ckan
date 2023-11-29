# encoding: utf-8

from ckan.common import config
from sqlalchemy.orm.session import SessionExtension
from paste.deploy.converters import asbool
import logging

log = logging.getLogger(__name__)


def activity_stream_item(obj, activity_type, revision, user_id):
    method = getattr(obj, "activity_stream_item", None)
    return method(activity_type, revision, user_id) if callable(method) else None


def activity_stream_detail(obj, activity_id, activity_type):
    method = getattr(obj, "activity_stream_detail", None)
    return method(activity_id, activity_type) if callable(method) else None


class DatasetActivitySessionExtension(SessionExtension):
    """Session extension that emits activity stream activities for packages
    and related objects.

    An SQLAlchemy SessionExtension that watches for new, changed or deleted
    Packages or objects with related packages (Resources, PackageExtras..)
    being committed to the SQLAlchemy session and creates Activity and
    ActivityDetail objects for these activities.

    For most types of activity the Activity and ActivityDetail objects are
    created in the relevant ckan/logic/action/ functions, but for Packages and
    objects with related packages they are created by this class instead.

    """
    def before_commit(self, session):
        if not asbool(config.get('ckan.activity_streams_enabled', 'true')):
            return

        session.flush()

        try:
            object_cache = session._object_cache
            revision = session.revision
        except AttributeError:
            # session had no _object_cache or no revision; skipping this commit
            return

        user_id = revision.user.id if revision.user else 'not logged in'
        # The top-level objects that we will append to the activity table. The
        # keys here are package IDs, and the values are model.activity:Activity
        # objects.
        activities = {}

        # The second-level objects that we will append to the activity_detail
        # table. Each row in the activity table has zero or more related rows
        # in the activity_detail table. The keys here are activity IDs, and the
        # values are lists of model.activity:ActivityDetail objects.
        activity_details = {}

        # Log new packages first to prevent them from getting incorrectly
        # logged as changed packages.
        # Looking for new packages...
        for obj in object_cache['new']:
            activity = activity_stream_item(obj, 'new', revision, user_id)
            if activity is None:
                continue
            # The object returns an activity stream item, so we know that the
            # object is a package.

            # Don't create activities for private datasets.
            if obj.private:
                continue

            activities[obj.id] = activity

            activity_detail = activity_stream_detail(obj, activity.id, "new")
            if activity_detail is not None:
                activity_details[activity.id] = [activity_detail]

        # Now process other objects.
        for activity_type in ('new', 'changed', 'deleted'):
            objects = object_cache[activity_type]
            for obj in objects:

                if not hasattr(obj, "id"):
                    # Object has no id; skipping
                    continue

                if (activity_type in ('new', 'changed') and
                        obj.id in activities):
                    # This object was already logged as a new package
                    continue

                try:
                    related_packages = obj.related_packages()
                except (AttributeError, TypeError):
                    # Object did not have a suitable related_packages() method;
                    # skipping it
                    continue

                for package in related_packages:
                    if package is None:
                        continue

                    # Don't create activities for private datasets.
                    if package.private:
                        continue

                    if package.id in activities:
                        activity = activities[package.id]
                    else:
                        activity = activity_stream_item(
                            package, "changed", revision, user_id)
                        if activity is None:
                            continue

                    activity_detail = activity_stream_detail(
                        obj, activity.id, activity_type)
                    if activity_detail is not None:
                        if package.id not in activities:
                            activities[package.id] = activity
                        if activity_details.has_key(activity.id):
                            activity_details[activity.id].append(
                                activity_detail)
                        else:
                            activity_details[activity.id] = [activity_detail]

        for activity in activities.values():
            # Emitting activity
            session.add(activity)

        for activity_detail_list in activity_details.values():
            for activity_detail_obj in activity_detail_list:
                # Emitting activity detail
                session.add(activity_detail_obj)

        session.flush()
