# Registries

This package contains Owlbot's internal registration and dispatch code for
events, commands, and HTTP routes. The module-facing API lives in `owlbot/api/`;
the classes here are the runtime layer that stores registrations, builds handler
contexts, and calls module code.

The goal of this README is to document the flows and contracts that maintainers
need to preserve. Function-level details should stay in docstrings or tests.

## Files

- `events.py`: event handler registration, priority ordering, propagation
  control, and command dispatch for chat events.
- `commands.py`: chat command registration, parsing, permission checks,
  cooldowns, aliases, and built-in commands.
- `routes.py`: module HTTP route registration, path matching, route guards,
  response handling, and shutdown tracking for active route handlers.
- `__init__.py`: convenience exports for the registry, dispatcher, and
  module-scoped wrapper classes.

Each domain has the same basic shape: decorators mark static handlers, module
wrappers support dynamic registration, registries store metadata, and dispatchers
build the right context before invoking the handler.

```mermaid
flowchart LR
    Decorator["@on_* decorator marks a handler"] --> Loader["ModuleLoader scans imported module"]
    Dynamic["module code registers handlers at runtime"] -->|ctx.commands/events/routes.register()| Dispatcher["domain dispatcher"]
    Loader --> Dispatcher
    Dispatcher --> Registry["domain registry stores metadata"]
    Registry --> Dispatcher
    Dispatcher --> Context["build Event/Command/Route context"]
    Context --> Handler["call module handler"]
```

## Core Contracts

Registry state is owned by a `ModuleLoader` instance. It is not global state.
This lets multiple Owlbot instances run in one Python process without sharing
registered handlers, cooldowns, or active route tasks.

Every registered command, event handler, and route belongs to a `module_name`.
The module-scoped wrappers (`ModuleCommands`, `ModuleEvents`, and
`ModuleRoutes`) automatically supply that name for dynamic registration and keep
modules from unregistering other modules' commands or event handlers.

During unload, `ModuleLoader._cleanup_module()` calls `unregister_by_module()`
on all three dispatchers so a module's registrations are removed together.

Dispatchers isolate module failures. A bad handler should be logged and handled
without bringing down the bot or corrupting the rest of the dispatch cycle.

## Event Dispatch

Events start as Owncast webhooks. `HttpServer._handle_webhook()` parses the
payload, starts event dispatch in a background task, and returns `202` to
Owncast immediately.

```mermaid
flowchart TD
    Owncast["Owncast webhook"] --> Server["HttpServer._handle_webhook()"]
    Server --> Parse["parse Owncast payload"]
    Parse --> Task["start event dispatch task"]
    Task --> Accepted["send 202 response to Owncast"]
    Task --> Events["run event dispatcher"]
    Events --> Refresh["refresh browser sessions for event user"]
    Refresh --> Registered{"any handlers for this event type?"}
    Registered -->|yes| Context["build EventContext"]
    Context --> Handler["run next handler in priority order"]
    Handler --> Stopped{"did a handler stop propagation?"}
    Stopped -->|yes| Skip["skip remaining handlers and commands"]
    Stopped -->|no| More{"any handlers left?"}
    More -->|yes| Handler
    More -->|no| Chat{"is this a chat event?"}
    Registered -->|no| Chat
    Chat -->|no| Done["done"]
    Chat -->|yes| Commands["delegate to command dispatcher"]
    Commands --> Done
    Skip --> Done
```

Event handlers run sequentially by priority. Higher priority runs first, and
handlers with the same priority run in registration order.

Event handlers receive `EventContext`, which contains the parsed event, the
owning `ModuleContext`, propagation state, and a session URL builder for events
that carry a user. Each handler gets its own context, but all handlers in one
dispatch share the same propagation state. Calling `ctx.stop_propagation()`
stops later event handlers and also prevents command dispatch for `CHAT` events.

For events that carry a `user`, dispatch refreshes any matching browser sessions
and exposes `ctx.session_url_for(path)`. Events without a user do not have a
session URL builder.

Handler exceptions and timeouts are logged. They do not stop later handlers
unless propagation was explicitly stopped.

## Command Dispatch

Commands are reached through event dispatch. The webhook server never calls the
command dispatcher directly. After `CHAT` event handlers finish, and only if
propagation was not stopped, `EventDispatcher` delegates to
`CommandDispatcher.dispatch()`.

```mermaid
flowchart TD
    Start["command dispatcher receives ChatEvent"] --> Parse["parse chat message text"]
    Parse --> Command{"message starts with command prefix?"}
    Command -->|no| Ignore["ignore message"]
    Command -->|yes| Lookup["look up trigger or alias"]
    Lookup --> Found{"does a command match?"}
    Found -->|no| Unknown["log debug and return"]
    Found -->|yes| Auth{"user passes auth check?"}
    Auth -->|no| AuthDeny["send private auth denial"]
    Auth -->|yes| Mod{"user passes moderator check?"}
    Mod -->|no| ModDeny["send private moderator denial"]
    Mod -->|yes| Cooldown{"command is off cooldown?"}
    Cooldown -->|no| CooldownDeny["send private cooldown message"]
    Cooldown -->|yes| Builtin{"built-in command?"}
    Builtin -->|yes| BuiltinHandler["call built-in handler"]
    Builtin -->|no| Context["build CommandContext"]
    Context --> Handler["call module handler"]
    AuthDeny --> Done["done"]
    ModDeny --> Done
    CooldownDeny --> Done
    Ignore --> Done
    Unknown --> Done
    BuiltinHandler --> Done
    Handler --> Done
```

Command parsing uses the configured prefix. The first token after the prefix is
the trigger, and the rest of the message becomes `ctx.args`. Matching is
case-insensitive.

Command names and aliases are globally unique across modules and built-ins.
Aliases resolve to the canonical command name, so handlers see the same
`ctx.command` no matter which trigger the user typed. Unregistering a command
also removes its aliases.

Permission checks and cooldowns happen before the handler runs. Cooldowns are
global per command, not per user, and the cooldown is claimed before invoking
the handler so concurrent messages cannot all slip through together.

Built-in commands use a smaller internal handler signature and do not receive a
`ModuleContext`. The default built-ins are:

- `about`: sends version and loaded-module information;
- `connect`: hides the invoking chat line and sends a one-time browser connect
  link to that client.

Module command handlers receive `CommandContext`, which contains the parsed
command, the original `ChatEvent`, the invoking user, and the owning
`ModuleContext`, plus a session URL builder for protected module routes.

Command handler exceptions and timeouts are logged and swallowed.

## Route Dispatch

`HttpServer` owns the aiohttp catch-all routes under `/owlbot/{module_name}` and
`/owlbot/{module_name}/{path:.*}`. Requests under those paths are delegated to
`RouteDispatcher.dispatch()`, which matches them against module-registered
routes.

```mermaid
flowchart TD
    Request["HTTP request under /owlbot/{module}"] --> Server["HttpServer catch-all"]
    Server --> Routes["run route dispatcher"]
    Routes --> FullPath["build module-namespaced path"]
    FullPath --> Match["match registered route and HTTP method"]
    Match --> Result{"route match result"}
    Result -->|no path| NotFound["404"]
    Result -->|method not allowed| MethodNotAllowed["405 with Allow header"]
    Result -->|handler found| Session["resolve browser session cookie"]
    Session --> Guards{"session/auth/moderator guards pass?"}
    Guards -->|no| Guidance["return 401 or 403 guidance page"]
    Guards -->|yes| Context["build RouteContext"]
    Context --> Streaming{"streaming route?"}
    Streaming -->|yes| StreamTask["track streaming task for shutdown"]
    Streaming -->|no| HandlerTask["track non-streaming task for shutdown"]
    StreamTask --> NoTimeout["call handler without timeout"]
    HandlerTask --> Timeout["call handler with timeout"]
    NoTimeout --> Normalize["normalize return value"]
    Timeout --> Normalize
    Normalize --> Response["HTTP response"]
```

Routes are automatically namespaced as `/owlbot/<module_name>/<path>`. They
support aiohttp-style path parameters such as `{id}` and `{id:regex}`. Route
matching is registration ordered, so broad patterns can shadow later, more
specific patterns.

Multiple handlers can share the same path when their HTTP methods do not
overlap. If a path matches but the method does not, dispatch returns `405` with
an `Allow` header. If no path matches, dispatch returns `404`.

Route guards run before the handler. `requires_session` needs any valid Owlbot
browser session; `requires_authenticated` and `requires_moderator` require an
authenticated user or moderator session. Any guard failure returns before the
handler is called. Missing or stale sessions return the shared connect guidance
page, while authentication and moderator failures return `403` guidance pages.

Route handlers receive `RouteContext`, which contains the aiohttp request, the
owning `ModuleContext`, captured path parameters, and the resolved browser
session if one exists.

Route handlers can return `None` for `204 No Content`, `web.StreamResponse`
objects as-is, or `dict[str, Any]` for JSON responses. Unsupported return
values, uncaught exceptions, and non-streaming timeouts become `500`. Raised
aiohttp `web.HTTPException` values pass through to aiohttp.

Streaming routes set `streaming=True`. They bypass the normal handler timeout
and are tracked separately so shutdown can cancel long-lived connections. During
shutdown, non-streaming handlers are allowed to finish first; streaming handlers
are cancelled afterward.

## Test Map

Start with these tests when changing registry behavior:

- `tests/test_events.py`: event registration, priority, propagation, command
  handoff, event contexts, and `ModuleEvents` ownership.
- `tests/test_commands.py`: command parsing, aliases, conflicts, permissions,
  cooldowns, built-ins, command contexts, and `ModuleCommands` ownership.
- `tests/test_routes.py`: route matching, method conflicts, guards, response
  handling, streaming, shutdown draining, route contexts, and `ModuleRoutes`
  ownership.
- `tests/test_module_loader.py`: loader wiring for shared session state and the
  configured command prefix.
- `tests/builtin_modules/`: integration-style coverage for built-in modules that
  use registry APIs through `ModuleContext`.
