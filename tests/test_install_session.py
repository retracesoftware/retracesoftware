import retracesoftware.utils as utils

from retracesoftware.install.session import InstallSession


def test_install_session_binds_wrapped_function_callbacks_and_records_wrapped_identity():
    session = InstallSession()

    def original(value):
        return value

    wrapped = utils.wrapped_function(
        target=original,
        handler=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    )

    bound = []
    session.register_wrapped_attr(owner=type("Owner", (), {}), name="callback", target=original, wrapped=wrapped)
    session.activate_callback_binding(bound.append)

    assert bound == [wrapped]
    assert session.normalize_record_callback(original) is wrapped
    assert session.normalize_replay_callback(wrapped) is wrapped


def test_install_session_round_trips_wrapped_member_callbacks():
    session = InstallSession()
    bound = []

    class Owner:
        value = property(lambda self: 1)

    target = Owner.__dict__["value"]
    wrapped = utils.wrapped_member(target=target, handler=lambda fn, *args, **kwargs: fn(*args, **kwargs))

    session.register_wrapped_attr(owner=Owner, name="value", target=target, wrapped=wrapped)
    session.activate_callback_binding(bound.append)

    recorded = session.normalize_record_callback(type(target).__get__)
    replay_fn = session.normalize_replay_callback(recorded)

    assert bound == [target, type(target).__get__, type(target).__set__, type(target).__delete__]
    assert recorded is type(target).__get__
    assert replay_fn is type(target).__get__
    assert replay_fn(target, Owner(), Owner) == 1


def test_install_session_binds_new_targets_immediately_while_active():
    session = InstallSession()
    bound = []
    session.activate_callback_binding(bound.append)

    def original():
        return None

    wrapped = utils.wrapped_function(target=original, handler=lambda fn, *args, **kwargs: fn(*args, **kwargs))
    session.register_wrapped_attr(owner=type("Owner", (), {}), name="callback", target=original, wrapped=wrapped)

    assert bound == [wrapped]
