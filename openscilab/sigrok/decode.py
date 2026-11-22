# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Decoding a capture by the names people use: a decoder by its id, its channels by id or name, its
options as values or text.

The Python API (``Capture.decode``), the command line and the ``decode.*`` nodes of the flows decode
through here (the flows must not depend on the API, which is a layer above them: ARCHITECTURE.md).
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Optional, Union
from typing import Sequence as SequenceType

from ..driver.models import AnalyzerChannel, CaptureSession
from .engine import DecoderInfo, DecoderRegistry, OptionType
from .provider import AnnotationGroup, DecoderInstance, SigrokProvider

_registries: dict[tuple[str, ...], DecoderRegistry] = {}


def decoder_registry(paths: SequenceType[str] = ()) -> DecoderRegistry:
    """The decoders of the usual search paths (and ``paths``), loaded once."""
    key = tuple(paths)
    registry = _registries.get(key)
    if registry is None:
        registry = _registries[key] = DecoderRegistry(paths)
    return registry


def decode_groups(
    session: CaptureSession,
    decoder: str,
    channels: Optional[Mapping[Union[str, int], Union[int, str]]] = None,
    options: Optional[Mapping[str, Any]] = None,
    stack: Iterable[Union[str, tuple[str, Mapping[str, Any]]]] = (),
    registry: Optional[DecoderRegistry] = None,
    run: Optional[Callable] = None,
) -> list[AnnotationGroup]:
    """Run ``decoder`` (with the decoders below it that provide its input, and ``stack`` on top) over
    ``session``: the annotations grouped by decoder (with ``error`` of each). ``channels`` maps the
    channels of the decoder (id, name or index) to channels of the capture (number or name), ``options``
    sets its options (text is converted to the option's type). ``run(provider, session)`` runs the
    decoders (default: in this thread; ``sigrok.worker.run``: in the decode process)."""
    registry = registry or decoder_registry()
    provider = SigrokProvider(registry)
    info = _decoder(registry, decoder)

    chain = [info]
    if not info.is_base_decoder:
        below = registry.provider_chain(info)
        if not below:
            raise ValueError(f"No installed decoder provides the input of '{info.id}' ({', '.join(info.inputs)}).")
        chain = below + [info]

    base = chain[0]
    parent = provider.add_instance(
        DecoderInstance(
            decoder_id=base.id,
            channel_map=_channel_map(session, base, channels or {}),
            options=_options(base, options if base is info else {}),
        )
    )
    for index, stacked in enumerate(chain[1:], start=1):
        parent = provider.add_instance(
            DecoderInstance(
                decoder_id=stacked.id,
                options=_options(stacked, options if stacked is info else {}),
                color_index=index,
                parent=parent,
            )
        )
    for index, item in enumerate(stack, start=len(chain)):
        stacked_id, stacked_options = (item, {}) if isinstance(item, str) else item
        stacked = _decoder(registry, stacked_id)
        parent = provider.add_instance(
            DecoderInstance(
                decoder_id=stacked.id,
                options=_options(stacked, stacked_options),
                color_index=index,
                parent=parent,
            )
        )
    return run(provider, session) if run is not None else provider.run(session)


def channel_of(session: CaptureSession, channel: Union[int, str]) -> AnalyzerChannel:
    """A channel by number (``3``) or name (``"SDA"``, ``"Channel 4"``)."""
    for item in session.capture_channels:
        if isinstance(channel, str):
            if channel in (item.display_name, item.channel_name, item.textual_channel_number):
                return item
        elif item.channel_number == int(channel):
            return item
    known = ", ".join(f"{item.channel_number} ({item.display_name})" for item in session.capture_channels)
    raise KeyError(f"The capture has no channel {channel!r}; channels: {known}.")


def _channel_map(session: CaptureSession, info: DecoderInfo, channels: Mapping[Any, Any]) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for key, value in channels.items():
        decoder_channel = _decoder_channel(info, key)
        try:
            capture_channel = channel_of(session, value if isinstance(value, str) and not value.isdigit() else int(value))
        except KeyError as error:
            raise ValueError(str(error.args[0])) from None
        mapping[decoder_channel] = capture_channel.channel_number
    missing = [channel.id for channel in info.required_channels if channel.index not in mapping]
    if missing:
        raise ValueError(
            f"Assign the channels {', '.join(missing)} of '{info.id}' "
            f"(e.g. channels={{'{missing[0]}': 0}})."
        )
    return mapping


def _decoder(registry: DecoderRegistry, decoder_id: str) -> DecoderInfo:
    info = registry.get(decoder_id)
    if info is None:
        raise ValueError(f"Decoder '{decoder_id}' is not installed (list them with decoders()).")
    return info


def _decoder_channel(info: DecoderInfo, key: Any) -> int:
    for channel in info.channels:
        if key in (channel.id, channel.name) or str(key).lower() in (channel.id.lower(), channel.name.lower()):
            return channel.index
    if isinstance(key, int) or str(key).isdigit():
        index = int(key)
        if any(channel.index == index for channel in info.channels):
            return index
    names = ", ".join(channel.id for channel in info.channels)
    raise ValueError(f"'{info.id}' has no channel '{key}' (channels: {names}).")


def _options(info: DecoderInfo, options: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {option.id: option for option in info.options}
    result = info.default_options()
    for key, value in (options or {}).items():
        option = by_id.get(key)
        if option is None:
            names = ", ".join(by_id) or "none"
            raise ValueError(f"'{info.id}' has no option '{key}' (options: {names}).")
        if isinstance(value, str):
            value = _option_value(option.option_type, option.default, value)
        if option.values and value not in option.values and str(value) not in [str(v) for v in option.values]:
            allowed = ", ".join(str(v) for v in option.values)
            raise ValueError(f"Invalid value '{value}' of option '{key}' of '{info.id}': use {allowed}.")
        result[key] = value
    return result


def _option_value(option_type: OptionType, default: Any, text: str) -> Any:
    """A value given as text (command line) in the type of the option."""
    reference = default if option_type == OptionType.LIST else None
    if option_type == OptionType.BOOLEAN or isinstance(reference, bool):
        lowered = text.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"Invalid yes/no value '{text}'.")
    try:
        if option_type == OptionType.INTEGER or isinstance(reference, int):
            try:
                return int(text, 0)  # 0x50 as well
            except ValueError:
                return int(text)
        if option_type == OptionType.DOUBLE or isinstance(reference, float):
            return float(text)
    except ValueError as error:
        raise ValueError(f"Invalid number '{text}'.") from error
    return text
