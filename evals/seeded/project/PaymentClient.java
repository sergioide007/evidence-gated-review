package demo.payments;

import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class PaymentClient {
    private static final int MAX_RETRIES = 3;
    private final HttpClient http = HttpClient.newHttpClient();

    public String charge(String customerId, long cents) throws Exception {
        HttpRequest req = HttpRequest.newBuilder()
            .uri(java.net.URI.create("https://pay.example.internal/charges"))
            .POST(HttpRequest.BodyPublishers.ofString("{\"customer\":\"" + customerId + "\",\"cents\":" + cents + "}"))
            .build();
        Exception last = null;
        for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
            try {
                return http.send(req, HttpResponse.BodyHandlers.ofString()).body();
            } catch (Exception e) {
                last = e; // timeout after the server accepted the charge is retried blindly
            }
        }
        throw last;
    }
}
