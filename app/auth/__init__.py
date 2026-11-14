"""Who is asking.

There are two ways a session starts, because there are two kinds of client.

KiCad signs the user in itself -- Authorization Code + PKCE against the
identity provider, through the system browser -- and hands this app the access
token. The app only has to decide whether that token is genuine and whose it
is, then turn it into a cookie the panel's WebView can carry.

A browser following a link to a part page has no KiCad to do that for it. The
link sits in a placed symbol's Datasheet field and may be followed from a PDF
years later. For that, this app is the OAuth2 client and runs the exchange.

Either way the user's credentials stay between them and the provider.

    identity   who a session belongs to
    oidc       the identity provider: checking tokens, running the exchange
    sessions   nonces, pending sign-ins and the sessions they become
    redirects  where a sign-in is allowed to land
"""
