# rest_api &middot; [![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](../LICENSE)
The rest_api within the intro_and_tutorials folder contains an introductory **tutorial notebook** to help you get started and learn how to access [Awesense](https://www.awesense.com)'s Energy Data Model (EDM) through the **REST APIs** by programmatic means.

A summary of the REST APIs capabilities is available on Awesense's **TGI Platform Documentation** page, which is accessible on all servers by clicking the question mark symbol located on the top right corner of the TGI UI.

Additional help with the REST APIs is available through Awesense's **api-connect developer portal**, a self-serve system for REST API documentation and key management. Access to this is included with a Sandbox subscription (or full platform license). Below is some information to help you make the most of it.

* When an account is made for you on the portal, you will receive an automated email inviting you to activate the account. Upon activation, you will be able to access the documentation of the Basic Data Retrieval REST API (i.e. endpoint set) under the “APIs” page. (Note: Awesense offers additional endpoint sets on non-sandbox servers. Contact us at api@awesense.com for details.)
* Click on the API to see the endpoints. Click on each endpoint to see parameters, responses, etc. Click “Try it” to actually execute an endpoint against a sandbox server (more on this below).
  * Tip: enable the “Group by tag” toggle for easier navigation.
* Under the “Products” page, you will be able to see on which sandbox server (tier 1 vs. tier 2&3) you are allowed to execute endpoint requests. Click on a product and then subscribe to it in order to obtain a key needed for endpoint execution. (Note: You can subscribe to the same product multiple times if you want different keys for different purposes, such as different use cases you may build using the APIs.)
  * You can see and manage all your subscriptions and associated keys under the “Profile” page.
* When using “Try it” to execute an endpoint, you must select one of the created subscription keys. Then you must also add a header with the name “Authorization” and a value populated with an encryption of your TGI username and password for the target sandbox server. You can obtain this using a tool like Postman (open a tab, go to Authorization, select Basic, and enter your TGI credentials; this will populate an authorization header for you in the Headers tab; copy over to the developer portal) — or similar. See screenshots below for guidance (actual UI may differ slightly).

Postman authorization credentials example:

<img src="screenshots/postman_authorization.png" width="800"/>

Postman authorization encrypted header example:

<img src="screenshots/postman_headers.png" width="800"/>

Developer portal "Try it" setup example:

<img src="screenshots/dev_portal_tryit.png" width="800"/>
