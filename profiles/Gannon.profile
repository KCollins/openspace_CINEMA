{
  "additional_scripts": [
    "openspace.setPropertyValueSingle(\"Scene.Earth.Renderable.Layers.NightLayers.Earth_at_Night_2012.Opacity\", 0.040000)",
    "openspace.fadeIn(\"Scene.Earth.Renderable.Layers.ColorLayers.Blue_Marble\")",
    "openspace.setPropertyValue('Scene.Earth.Renderable.Layers.ColorLayers.ESRI_VIIRS_Combo.Enabled', false)",
    "openspace.setPropertyValueSingle(\"Scene.NOAA_GloTEC_Ionosphere.Renderable.Opacity\", 0.320000)",
    "openspace.setPropertyValueSingle(\"OpenSpaceEngine.PropertyVisibility\", 5.000000)",
    "openspace.action.triggerAction(\"os.FadeDownTrails\")"
  ],
  "addons": {
    "recommended": [
      "addons/asteroids",
      "addons/dwarf_planets",
      "addons/interstellar_objects",
      "addons/minor_moons",
      "addons/desi"
    ]
  },
  "assets": [
    "base",
    "base_keybindings",
    "events/toggle_sun",
    "openspace_CINEMA/Aurora/aurora_mosaic_20240510_20240515",
    "openspace_CINEMA/Aurora/OVATION/aurora_oval",
    "openspace_CINEMA/TEC/glotec_ionosphere",
    "scene/solarsystem/planets/earth/earth",
    "scene/solarsystem/planets/earth/noaa-sos/overlays/latlon_grid-white",
    "scene/solarsystem/planets/earth/satellites/satellites"
  ],
  "camera": {
    "altitude": 17000000.0,
    "anchor": "Earth",
    "latitude": 58.5877,
    "longitude": 16.1924,
    "type": "goToGeo"
  },
  "delta_times": [
    1.0,
    5.0,
    30.0,
    60.0,
    300.0,
    1800.0,
    3600.0,
    43200.0,
    86400.0,
    604800.0,
    1209600.0,
    2592000.0,
    5184000.0,
    7776000.0,
    15552000.0,
    31536000.0,
    63072000.0,
    157680000.0,
    315360000.0,
    630720000.0
  ],
  "mark_nodes": [
    "Earth",
    "Mars",
    "Moon",
    "Sun",
    "Venus",
    "ISS"
  ],
  "meta": {
    "author": "OpenSpace Team",
    "description": "Default OpenSpace Profile. Adds Earth satellites not contained in other profiles.",
    "license": "MIT License",
    "name": "Default",
    "url": "https://www.openspaceproject.com",
    "version": "1.0"
  },
  "properties": [
    {
      "name": "{earth_satellites~space_stations}.Renderable.Enabled",
      "type": "setPropertyValue",
      "value": "false"
    },
    {
      "name": "Scene.Earth.Renderable.Layers.Overlays.noaa-sos-overlays-latlon_grid-white.Enabled",
      "type": "setPropertyValueSingle",
      "value": "false"
    }
  ],
  "time": {
    "is_paused": false,
    "type": "absolute",
    "value": "2024-05-10T11:58:42"
  },
  "version": {
    "major": 1,
    "minor": 5
  }
}