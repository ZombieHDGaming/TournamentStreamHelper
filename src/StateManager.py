import os
import orjson
import traceback
from deepdiff import DeepDiff, extract
from functools import partial
import shutil
import threading
import requests
from PIL import Image
import time
from loguru import logger
from .Helpers.TSHDictHelper import deep_get, deep_set, deep_unset, deep_clone
from .SettingsManager import SettingsManager


class StateManager:
    lastSavedState = {}
    state = {}
    saveBlocked = 0
    webServer = None
    changedKeys = []

    lock = threading.RLock()
    threads = []
    loop = None

    def BlockSaving():
        StateManager.saveBlocked += 1
        logger.warning(
            "Initial Block - Current Blocking Status: " + str(StateManager.saveBlocked))

    def ReleaseSaving():
        StateManager.saveBlocked -= 1
        logger.warning(
            "Release Block - Current Blocking Status: " + str(StateManager.saveBlocked))
        if StateManager.saveBlocked == 0:
            StateManager.SaveState()

    def SaveState():
        if StateManager.saveBlocked == 0:
            with StateManager.lock:
                StateManager.threads = []

                def ExportAll(ref_diff):
                    with open("./out/program_state.json", 'wb', buffering=8192) as file:
                        # logger.info("SaveState")
                        StateManager.state.update({"timestamp": time.time()})
                        file.write(orjson.dumps(
                            StateManager.state, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_INDENT_2))
                        StateManager.state.pop("timestamp")

                    if not SettingsManager.Get("general.disable_export", False):
                        StateManager.ExportText(
                            StateManager.lastSavedState, ref_diff)
                    StateManager.lastSavedState = deep_clone(
                        StateManager.state)

                # logger.debug(StateManager.changedKeys)

                diff = DeepDiff(
                    StateManager.lastSavedState,
                    StateManager.state,
                    include_paths=StateManager.changedKeys
                )

                StateManager.changedKeys = []

                if len(diff) > 0:
                    try:
                        if StateManager.webServer is not None:
                            StateManager.webServer.emit(
                                'program_state', StateManager.state)
                    except Exception as e:
                        logger.error(traceback.format_exc())

                    exportThread = threading.Thread(
                        target=partial(ExportAll, ref_diff=diff))
                    StateManager.threads.append(exportThread)
                    exportThread.start()

                    for t in StateManager.threads:
                        t.join()

    def LoadState():
        try:
            with open("./out/program_state.json", 'rb') as file:
                StateManager.state = orjson.loads(file.read())
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(traceback.format_exc())
            StateManager.state = {}
            StateManager.SaveState()

    def Set(key: str, value):
        # logger.debug(f"StateManager Setting {key} to {value}")
        with StateManager.lock:
            # StateManager.lastSavedState = deep_clone(StateManager.state)

            deep_set(StateManager.state, key, value)

            final_key = "root"
            for k in key.split("."):
                final_key += f"['{k}']"

            StateManager.changedKeys.append(final_key)

            if StateManager.saveBlocked == 0:
                StateManager.SaveState()
                # StateManager.ExportText(oldState)

    def Unset(key: str):
        with StateManager.lock:
            # StateManager.lastSavedState = deep_clone(StateManager.state)
            deep_unset(StateManager.state, key)

            final_key = "root"
            for k in key.split("."):
                final_key += f"['{k}']"
            StateManager.changedKeys.append(final_key)

            if StateManager.saveBlocked == 0:
                StateManager.SaveState()
                # StateManager.ExportText(oldState)

    def Get(key: str, default=None):
        return deep_get(StateManager.state, key, default)

    def ExportText(oldState, diff):
        # logger.info("ExportState")
        # logger.info(diff)

        mergedDiffs = list(diff.get("values_changed", {}).items())
        mergedDiffs.extend(list(diff.get("type_changes", {}).items()))

        # logger.info(mergedDiffs)

        for changeKey, change in mergedDiffs:
            # Remove "root[" from start and separate keys
            filename = "/".join(changeKey[5:].replace(
                "'", "").replace("]", "").replace("/", "_").split("["))

            # logger.info(filename)

            if change.get("new_type") == type(None):
                StateManager.RemoveFilesDict(
                    filename, extract(oldState, changeKey))
            else:
                StateManager.CreateFilesDict(
                    filename, change.get("new_value"))

        removedKeys = diff.get("dictionary_item_removed", {})

        for key in removedKeys:
            item = extract(oldState, key)

            # Remove "root[" from start and separate keys
            filename = "/".join(key[5:].replace(
                "'", "").replace("]", "").replace("/", "_").split("["))

            # logger.info("Removed:", filename, item)

            StateManager.RemoveFilesDict(filename, item)

        addedKeys = diff.get("dictionary_item_added", {})

        for key in addedKeys:
            try:
                item = extract(StateManager.state, key)

                # Remove "root[" from start and separate keys
                path = "/".join(key[5:].replace(
                    "'", "").replace("]", "").replace("/", "_").split("["))
                # Remove "root[" from start and separate keys
                path = "/".join(key[5:].replace(
                    "'", "").replace("]", "").replace("/", "_").split("["))

                # logger.info("Added:", path, item)
                # logger.info("Added:", path, item)

                StateManager.CreateFilesDict(path, item)
            except Exception as e:
                logger.error(traceback.format_exc())

    def CreateFilesDict(path, di):
        pathdirs = "/".join(path.split("/")[0:-1])

        if not os.path.isdir("./out/"+pathdirs):
            os.makedirs("./out/"+pathdirs)

        if type(di) == dict:
            for k, i in di.items():
                StateManager.CreateFilesDict(
                    path+"/"+str(k).replace("/", "_"), i)
        else:
            # logger.info("try to add: ", path)
            if type(di) == str and di.startswith("./"):
                if os.path.exists(f"./out/{path}" + "." + di.rsplit(".", 1)[-1]):
                    try:
                        os.remove(f"./out/{path}" + "." +
                                  di.rsplit(".", 1)[-1])
                    except Exception as e:
                        logger.error(traceback.format_exc())
                if os.path.exists(di):
                    try:
                        shutil.copyfile(
                            os.path.abspath(di),
                            f"./out/{path}" + "." + di.rsplit(".", 1)[-1])
                    except Exception as e:
                        logger.error(traceback.format_exc())
            elif type(di) == str and di.startswith("http") and (di.endswith(".png") or di.endswith(".jpg")):
                try:
                    if os.path.exists(f"./out/{path}" + "." + di.rsplit(".", 1)[-1]):
                        try:
                            os.remove(f"./out/{path}" +
                                      "." + di.rsplit(".", 1)[-1])
                        except Exception as e:
                            logger.error(traceback.format_exc())

                    def downloadImage(url, dlpath):
                        try:
                            r = requests.get(url, stream=True)
                            if r.status_code == 200:
                                with open(dlpath, 'wb') as f:
                                    r.raw.decode_content = True
                                    shutil.copyfileobj(r.raw, f)
                                    f.flush()
                            if url.endswith(".jpg"):
                                original = Image.open(dlpath)
                                original.save(dlpath.rsplit(
                                    ".", 1)[0]+".png", format="png")
                                os.remove(dlpath)
                        except Exception as e:
                            logger.error(traceback.format_exc())

                    t = threading.Thread(
                        target=downloadImage,
                        args=[
                            di,
                            f"./out/{path}" + "." + di.rsplit(".", 1)[-1]
                        ]
                    )
                    StateManager.threads.append(t)
                    t.start()
                except Exception as e:
                    logger.error(traceback.format_exc())
            else:
                with open(f"./out/{path}.txt", 'w', encoding='utf-8') as file:
                    file.write(str(di))

    def RemoveFilesDict(path, di):
        pathdirs = "/".join(path.split("/")[0:-1])

        if type(di) == dict:
            for k, i in di.items():
                StateManager.RemoveFilesDict(
                    path+"/"+str(k).replace("/", "_"), i)
        else:
            if type(di) == str and (di.startswith("./") or di.startswith("http")):
                try:
                    removeFile = f"./out/{path}" + \
                        "." + di.rsplit(".", 1)[-1]
                    # logger.info("try to remove: ", removeFile)
                    if os.path.exists(removeFile):
                        os.remove(removeFile)
                except:
                    logger.error(traceback.format_exc())
            else:
                try:
                    removeFile = f"./out/{path}.txt"
                    # logger.info("try to remove: ", removeFile)
                    if os.path.exists(removeFile):
                        os.remove(removeFile)
                except:
                    logger.error(traceback.format_exc())

        try:
            # logger.info("Remove path", f"./out/{path}")
            if os.path.exists(f"./out/{path}"):
                shutil.rmtree(f"./out/{path}")
        except:
            logger.error(traceback.format_exc())


if not os.path.exists("./out"):
    os.makedirs("./out/")

if not os.path.isfile("./out/program_state.json"):
    StateManager.SaveState()

StateManager.LoadState()
