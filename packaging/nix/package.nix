# uncord and the three python libraries it needs that nixpkgs doesn't have yet
{
  lib,
  python3Packages,
  fetchFromGitHub,
  fetchPypi,
  rustPlatform,
  makeWrapper,
  ffmpeg,
  libopus,
  chafa,
  pipewire,
  mpv,
  wl-clipboard,
  gpu-screen-recorder,
  withScreenShare ? true,
}:

let
  py = python3Packages;

  davey = py.buildPythonPackage rec {
    pname = "davey";
    version = "0.1.7";
    pyproject = true;
    src = fetchPypi {
      inherit pname version;
      hash = "sha256-Daxoxrqhy/0QSLh1JtKckYT+t9gXX13CQd80jEMHqMA=";
    };
    cargoDeps = rustPlatform.fetchCargoVendor {
      inherit pname version src;
      hash = "sha256-3xgP0lZ/dhPOnGIjuYdXROvJogtHBuqAmqEKMWQ8YYo=";
    };
    nativeBuildInputs = [
      rustPlatform.cargoSetupHook
      rustPlatform.maturinBuildHook
    ];
    pythonImportsCheck = [ "davey" ];
    meta = {
      description = "Discord's DAVE end to end encryption for voice and video";
      homepage = "https://github.com/Snazzah/davey";
      license = lib.licenses.mit;
    };
  };

  discord-protos = py.buildPythonPackage rec {
    pname = "discord-protos";
    version = "0.0.2";
    pyproject = true;
    src = fetchPypi {
      inherit pname version;
      hash = "sha256-I5U6BfMr7ttAtwjsS0V1MKYZaknI110zeukoKipByZc=";
    };
    build-system = [ py.setuptools ];
    dependencies = [ py.protobuf ];
    pythonImportsCheck = [ "discord_protos" ];
    meta = {
      description = "Discord's protobuf definitions for Python";
      homepage = "https://github.com/dolfies/discord-protos";
      license = lib.licenses.mit;
    };
  };

  discord-py-self = py.buildPythonPackage {
    pname = "discord.py-self";
    version = "2.2.0-unstable-2026-10-10";
    pyproject = true;
    src = fetchFromGitHub {
      owner = "dolfies";
      repo = "discord.py-self";
      rev = "2ba64a9a997e151a9c259984e0a179b1fdf4aff4";
      hash = "sha256-3SzDET0R+x79pmzPL8FEWYHfKG43ySEZlmQ+3spErhg=";
    };
    build-system = [ py.setuptools ];
    dependencies = with py; [
      aiohttp
      curl-cffi
      tzlocal
      discord-protos
      pynacl
      davey
    ] ++ lib.optional (py.pythonAtLeast "3.13") py.audioop-lts;
    pythonRelaxDeps = true;
    pythonImportsCheck = [ "discord" ];
    meta = {
      description = "Python wrapper for the Discord user API";
      homepage = "https://github.com/dolfies/discord.py-self";
      license = lib.licenses.mit;
    };
  };

  runtimeBins = [ ffmpeg pipewire mpv wl-clipboard ] ++ lib.optional withScreenShare gpu-screen-recorder;
in
py.buildPythonApplication {
  pname = "uncord";
  version = builtins.head (builtins.match ".*__version__ = \"([^\"]+)\".*" (builtins.readFile ../../uncord/__init__.py));
  pyproject = true;

  src = lib.cleanSourceWith {
    src = ../..;
    filter = path: type:
      !(builtins.elem (baseNameOf path) [ ".venv" "build" ".git" "result" "uncord.egg-info" ".ffmpeg-bin" ".libopus" ".chafa" ])
      && !(lib.hasSuffix ".so" path);
  };

  build-system = [ py.setuptools ];
  nativeBuildInputs = [ makeWrapper ];

  dependencies = with py; [
    discord-py-self
    textual
    rich
    textual-image
    pillow
    pynacl
    davey
    cryptography
    segno
    jeepney
  ];

  pythonRelaxDeps = [ "textual" ];
  # discord.py-self comes from git, which pip metadata can't express for nix
  pythonRemoveDeps = [ "discord.py-self" ];

  makeWrapperArgs = [
    # after the system path so setcap wrappers like programs.gpu-screen-recorder still win
    "--suffix PATH : ${lib.makeBinPath runtimeBins}"
    "--set-default UNCORD_FFMPEG ${lib.getExe ffmpeg}"
    "--set-default UNCORD_OPUS ${lib.getLib libopus}/lib/libopus.so.0"
    "--set-default UNCORD_CHAFA ${lib.getLib chafa}/lib/libchafa.so.0"
  ];

  postInstall = ''
    install -Dm644 assets/icon.svg $out/share/icons/hicolor/scalable/apps/uncord.svg
    install -Dm644 packaging/uncord.desktop $out/share/applications/uncord.desktop
  '';

  pythonImportsCheck = [ "uncord" "uncord._native" "uncord._dsp" ];

  passthru = { inherit davey discord-protos discord-py-self; };

  meta = {
    description = "Discord in your terminal, with voice calls, inline images and screen sharing";
    homepage = "https://github.com/epwv/uncord";
    license = lib.licenses.mit;
    mainProgram = "uncord";
    platforms = lib.platforms.linux;
  };
}
