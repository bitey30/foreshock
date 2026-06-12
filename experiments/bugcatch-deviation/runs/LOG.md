# foreshock hardening loop

Varied public repos analyzed to find where foreshock breaks. Each run: clone → analyze → log → delete.

### Run 1: https://github.com/statelyai/xstate
- files=283, edges=375, resolve_rate=0.938 (unresolved 25/403)
- aliases: ['@xstate/store', '@xstate/store-react']
- top fan-in: 37×symbolObservable.ts, 37×reportUnhandledError.ts, 37×memo.ts, 37×constants.ts
- completeness sites: 6×stateUtils.ts, 3×createActor.ts

### Run 2: https://github.com/pallets/flask  [python]
- files=35, edges=100, resolve_rate=1.0 (unresolved 0/176)
- aliases: none
- top fan-in: 22×typing.py, 22×signals.py, 21×wrappers.py, 21×testing.py
- completeness sites: 3×cli.py, 3×app.py

### Run 3: https://github.com/sindresorhus/ky  [ts]
- graph: files=29, edges=70, resolve_rate=1.0, completeness_sites=0
- **benchmark — git co-change recall: 66/66 = 1.0** (214 coupled pairs / 301 commits; 148 unindexed)
- checks 4/4: resolve_rate>=0.85=PASS, no_self_edges=PASS, graph_nonempty=PASS, cochange_signal=PASS

### Run 4: https://github.com/google/gson  [java]
- graph: files=121, edges=407, resolve_rate=0.853
- **foreshock: P=0.058 R=0.629 F1=0.106** | baseline(same-dir): P=0.065 R=0.355 F1=0.11 | lift_F1=-0.004  (15 seeds, 222 commits)
- checks 3/4: resolve_rate>=0.85=PASS, graph_nonempty=PASS, enough_seeds=PASS, beats_samedir_baseline=FAIL
- ⚠️ COVERAGE FAILURES (co-changed with seed but NOT in foreshock's blast radius):
    - Gson.java ⇸ JsonArray.java, JsonObject.java, JsonParser.java (+7)
    - GsonBuilder.java ⇸ FormattingStyle.java, JsonPrimitive.java, JsonReader.java (+4)
    - ReflectiveTypeAdapterFactory.java ⇸ ConstructorConstructor.java, ReflectionHelper.java (+2)
    - JsonArray.java ⇸ JsonObject.java, JsonPrimitive.java (+2)
    - TreeTypeAdapter.java ⇸ LinkedTreeMap.java, JsonReader.java (+2)

### Run 5: https://github.com/psf/requests  [python]
- graph: files=22, edges=72, resolve_rate=1.0
- **foreshock: P=0.707 R=0.702 F1=0.704** | baseline(same-dir): P=0.53 R=1.0 F1=0.693 | lift_F1=0.011  (13 seeds, 284 commits)
- checks 4/4: resolve_rate>=0.85=PASS, graph_nonempty=PASS, enough_seeds=PASS, beats_samedir_baseline=PASS
- ⚠️ COVERAGE FAILURES (co-changed with seed but NOT in foreshock's blast radius):
    - models.py ⇸ _internal_utils.py, compat.py (+2)
    - sessions.py ⇸ _internal_utils.py, _types.py, auth.py (+6)
    - utils.py ⇸ _internal_utils.py, compat.py, help.py (+3)
    - adapters.py ⇸ _internal_utils.py, compat.py, help.py (+3)
    - __init__.py ⇸ _internal_utils.py, compat.py (+2)

### Run 6: https://github.com/django/django  [python]
- graph: files=912, edges=3025, resolve_rate=0.999
- **foreshock: P=None R=None F1=None** | baseline(same-dir): P=None R=None F1=None | lift_F1=None  (0 seeds, 326 commits)
- checks 2/4: resolve_rate>=0.85=PASS, graph_nonempty=PASS, enough_seeds=FAIL, beats_samedir_baseline=FAIL
