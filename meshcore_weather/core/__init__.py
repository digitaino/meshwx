"""Core service layer: ONE implementation per product, shared by every
consumer (text commands, scheduled broadcasts, on-demand requests).

    location.resolve_location()  -> Location   (where is the user asking about)
    services.observation_for()   -> Observation
    services.forecast_for()      -> Forecast
    services.warnings_for()      -> list[Warning dict]

    render_text.*                -> one-packet text for humans
    (protocol.meshwx / encoders) -> bytes for apps

The rule: a product is parsed once into a canonical object; text and binary
are two renderings of that same object and can never disagree.
"""
