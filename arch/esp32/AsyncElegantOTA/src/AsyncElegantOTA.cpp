#include "AsyncElegantOTA.h"
#include <atomic>
#if defined(ESP32)
#include <helpers/esp32/ManagementPassword.h>
#endif
static std::atomic<bool> ota_uploads_enabled{false};
static std::atomic<bool> ota_upload_busy{false};
#if defined(ESP32)
static std::atomic<bool> ota_reboot_pending{false};
#endif
// All upload callbacks run on AsyncTCP's task. Update is a global singleton;
// retain one request as its owner until that request disconnects.
static AsyncWebServerRequest* ota_upload_owner = nullptr;
struct OtaUploadState {
    bool file_final;
    bool committed;
};

AsyncElegantOtaClass AsyncElegantOTA;

void AsyncElegantOtaClass::setID(const char* id){
    _id = id;
}

bool AsyncElegantOtaClass::authenticateRequest(AsyncWebServerRequest* request,
                                               uint32_t* generation) {
#if defined(ESP32)
    if (_managementPassword) {
        const char* authorization = request->hasHeader("Authorization")
            ? request->getHeader("Authorization")->value().c_str() : nullptr;
        const auto result = mesh::esp32::managementPassword::authenticateBasic(
            authorization, millis(), generation);
        if (result == mesh::esp32::managementPassword::AuthResult::Authorized) return true;
        if (result == mesh::esp32::managementPassword::AuthResult::Unavailable)
            request->send(503, "text/plain", "saved admin password unavailable");
        else if (result == mesh::esp32::managementPassword::AuthResult::Throttled)
            request->send(429, "text/plain", "too many failed logins; wait 30 seconds");
        else {
            AsyncWebServerResponse* response = request->beginResponse(401, "text/plain", "Sign in with username admin and this node's admin password.");
            response->addHeader("WWW-Authenticate", "Basic realm=\"MeshCore (username: admin)\", charset=\"UTF-8\"");
            response->addHeader("Cache-Control", "no-store");
            request->send(response);
        }
        return false;
    }
#endif
    if (_authRequired && !request->authenticate(_username.c_str(), _password.c_str())) {
        request->requestAuthentication();
        return false;
    }
    return true;
}

void AsyncElegantOtaClass::begin(AsyncWebServer *server, const char* username, const char* password){
    _server = server;
    setEnabled(true);

    if(strlen(username) > 0){
        _authRequired = true;
        _username = username;
        _password = password;
    }else{
        _authRequired = false;
        _username = "";
        _password = "";
    }

    _server->on("/update/identity", HTTP_GET, [&](AsyncWebServerRequest *request){
        if (!authenticateRequest(request)) return;
        #if defined(ESP8266)
            request->send(200, "application/json", "{\"id\": \""+_id+"\", \"hardware\": \"ESP8266\"}");
        #elif defined(ESP32)
            request->send(200, "application/json", "{\"id\": \""+_id+"\", \"hardware\": \"ESP32\"}");
        #endif
    });

    _server->on("/update", HTTP_GET, [&](AsyncWebServerRequest *request){
        if (!authenticateRequest(request)) return;
        AsyncWebServerResponse *response = request->beginResponse_P(200, "text/html", ELEGANT_HTML, ELEGANT_HTML_SIZE);
        response->addHeader("Content-Encoding", "gzip");
        request->send(response);
    });

    _server->on("/update", HTTP_POST, [&](AsyncWebServerRequest *request) {
        if (!authenticateRequest(request)) return;
        // send() queues a response until the request body has finished. Keep
        // an earlier upload error even before it has reached the wire.
        if (request->isSent() || request->getResponse()) return;
        auto* state = static_cast<OtaUploadState*>(request->_tempObject);
        // A file boundary can precede more multipart fields or another file.
        // Update.end() selects the next boot partition, so commit only after
        // the whole POST has completed without an earlier upload error.
        if (state && state->file_final && !state->committed
            && ota_upload_owner == request && !Update.hasError()) {
            if (!Update.end(true)) {
                return request->send(400, "text/plain", "Could not end OTA");
            }
            state->committed = true;
        }
        const bool complete = state && state->committed && !Update.hasError();
        AsyncWebServerResponse *response = request->beginResponse(complete?200:500, "text/plain", complete?"OK":"FAIL");
        response->addHeader("Connection", "close");
        response->addHeader("Access-Control-Allow-Origin", "*");
        request->send(response);
    }, [&](AsyncWebServerRequest *request, String filename, size_t index, uint8_t *data, size_t len, bool final) {
        //Upload handler chunks in data
        if (request->isSent() || request->getResponse()) return;
        uint32_t authenticated_generation = 0;
        if (!authenticateRequest(request, &authenticated_generation)) return;

        if (!index) {
            if (!ota_uploads_enabled.load()) {
                return request->send(503, "text/plain", "OTA stopped");
            }
            bool expected = false;
            if (request->_tempObject
#if defined(ESP32)
                || ota_reboot_pending.load()
#endif
                || !ota_upload_busy.compare_exchange_strong(expected, true)
            ) {
                return request->send(409, "text/plain", "OTA upload already active");
            }
            // A stop can race the first check before ownership was reserved.
            if (!ota_uploads_enabled.load()) {
                ota_upload_busy.store(false);
                return request->send(503, "text/plain", "OTA stopped");
            }
            if(!request->hasParam("MD5", true)) {
                ota_upload_busy.store(false);
                return request->send(400, "text/plain", "MD5 parameter missing");
            }

#if defined(ESP32)
            if (_managementPassword && !mesh::esp32::managementPassword::beginUpload(authenticated_generation)) {
                ota_upload_busy.store(false);
                return request->send(409, "text/plain", "password change or OTA upload in progress; retry");
            }
#endif

            #if defined(ESP8266)
                int cmd = (filename == "filesystem") ? U_FS : U_FLASH;
                Update.runAsync(true);
                size_t fsSize = ((size_t) &_FS_end - (size_t) &_FS_start);
                uint32_t maxSketchSpace = (ESP.getFreeSketchSpace() - 0x1000) & 0xFFFFF000;
                if (!Update.begin((cmd == U_FS)?fsSize:maxSketchSpace, cmd)){ // Start with max available size
            #elif defined(ESP32)
                int cmd = (filename == "filesystem") ? U_SPIFFS : U_FLASH;
                if (!Update.begin(UPDATE_SIZE_UNKNOWN, cmd)) { // Start with max available size
            #endif
                ota_upload_busy.store(false);
#if defined(ESP32)
                if (_managementPassword) mesh::esp32::managementPassword::endUpload();
#endif
                return request->send(400, "text/plain", "OTA could not begin");
            }
            // Arduino Update.begin() clears the expected digest. Set it only
            // after begin succeeds so end() actually verifies the uploaded body.
            if(!Update.setMD5(request->getParam("MD5", true)->value().c_str())) {
                Update.abort();
                ota_upload_busy.store(false);
#if defined(ESP32)
                if (_managementPassword) mesh::esp32::managementPassword::endUpload();
#endif
                return request->send(400, "text/plain", "MD5 parameter invalid");
            }

            // The HTTP listener defaults to a three-second receive timeout.
            // A valid OTA body can pause longer on a weak WiFi link; retain a
            // bounded timeout on this admitted upload without changing peers.
            request->client()->setRxTimeout(30);

            // AsyncWebServer frees this per-request allocation on disconnect.
            // hasError() alone also reports success for a POST with no upload.
            request->_tempObject = calloc(1, sizeof(OtaUploadState));
            if (!request->_tempObject) {
                Update.abort();
                ota_upload_busy.store(false);
#if defined(ESP32)
                if (_managementPassword) mesh::esp32::managementPassword::endUpload();
#endif
                return request->send(503, "text/plain", "OTA state allocation failed");
            }
            ota_upload_owner = request;
            request->onDisconnect([this, request]() {
                if (ota_upload_owner != request) return;
                ota_upload_owner = nullptr;
                if (static_cast<OtaUploadState*>(request->_tempObject)->committed) {
                    // Let the response drain and the network callback return
                    // before rebooting from the separate restart task.
                    restart();
                } else {
                    Update.abort(); // Release a disconnected, partial upload.
                }
                ota_upload_busy.store(false);
#if defined(ESP32)
                if (_managementPassword) mesh::esp32::managementPassword::endUpload();
#endif
            });
        }

        if (request->isSent() || ota_upload_owner != request) return;

        // Write chunked data to the free sketch space
        if(len){
            if (Update.write(data, len) != len) {
                return request->send(400, "text/plain", "OTA flash write failed");
            }
        }
            
        if (final) {
            static_cast<OtaUploadState*>(request->_tempObject)->file_final = true;
        }
    });
}

bool AsyncElegantOtaClass::setEnabled(bool enabled) {
    if (enabled) {
        ota_uploads_enabled.store(true);
        return true;
    }
    const bool was_enabled = ota_uploads_enabled.exchange(false);
    if (ota_upload_busy.load()
#if defined(ESP32)
        || ota_reboot_pending.load()
#endif
    ) {
        ota_uploads_enabled.store(was_enabled);
        return false;
    }
    return true;
}

// deprecated, keeping for backward compatibility
void AsyncElegantOtaClass::loop() {
}

void AsyncElegantOtaClass::restart() {
#if defined(ESP32)
    bool expected = false;
    if (!ota_reboot_pending.compare_exchange_strong(expected, true)) return;
    // Even disconnect handlers run on the shared AsyncTCP task. Return to it
    // immediately, then reboot from a separate task after final TCP cleanup.
    if (xTaskCreate([](void*) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            ESP.restart();
            vTaskDelete(nullptr);
        }, "ota-reboot", 3072, nullptr, 1, nullptr) != pdPASS) {
        ota_reboot_pending.store(false);
    }
#else
    yield();
    delay(1000);
    yield();
    ESP.restart();
#endif
}

String AsyncElegantOtaClass::getID(){
    String id = "";
    #if defined(ESP8266)
        id = String(ESP.getChipId());
    #elif defined(ESP32)
        id = String((uint32_t)ESP.getEfuseMac(), HEX);
    #endif
    id.toUpperCase();
    return id;
}
