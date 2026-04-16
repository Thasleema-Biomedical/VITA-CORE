// VITA-CORE ESP32 Biomedical Data Acquisition
// Connect AD8232 OUT pin to ESP32 Pin 25

const int AD8232_PIN = 25;
const int SAMPLE_RATE_HZ = 200;
const int DELAY_MS = 1000 / SAMPLE_RATE_HZ; // 5ms per sample

void setup() {
  Serial.begin(115200);
  pinMode(AD8232_PIN, INPUT);
}

void loop() {
  // Read the analog value from AD8232 connected to D25
  int sensorValue = analogRead(AD8232_PIN);

  // Print single value to Serial, the Python script will use this 
  // single signal for both gastric and pulse components 
  Serial.println(sensorValue);

  // Wait to maintain 200Hz sample rate
  delay(DELAY_MS);
}
