#include <TM1637Display.h>

// Connect AD8232 OUT to A6 (or change to A0)
const int AD8232_PIN = A6; 
// Connect AD8232 LO+ to D10 and LO- to D11
const int LO_PLUS_PIN = 10;
const int LO_MINUS_PIN = 11;

// TM1637 Display pins
// Connect CLK to D4, DIO to D5
const int CLK_PIN = 4;
const int DIO_PIN = 5;
TM1637Display display(CLK_PIN, DIO_PIN);

// LED Pins
const int LED_GREEN_PIN = 8;
const int LED_RED_PIN = 9;

const int SAMPLE_RATE_HZ = 200;
const int DELAY_MS = 1000 / SAMPLE_RATE_HZ; // 5ms per sample

String inputString = "";

void setup() {
  Serial.begin(115200);
  pinMode(AD8232_PIN, INPUT);
  pinMode(LO_PLUS_PIN, INPUT);
  pinMode(LO_MINUS_PIN, INPUT);

  pinMode(LED_GREEN_PIN, OUTPUT);
  pinMode(LED_RED_PIN, OUTPUT);

  display.setBrightness(0x0a); // Set brightness
  display.clear();
  display.showNumberDec(0, false); // Start with 0
}

unsigned long lastSampleTime = 0;
const unsigned long sampleIntervalMicros = 1000000 / SAMPLE_RATE_HZ; // 5000 us

unsigned long lastBlinkTime = 0;
char currentTriage = 'G'; // 'G', 'Y', 'R'
bool ledState = false;

void loop() {
  unsigned long currentMicros = micros();
  
  // Only sample if 5000 microseconds have passed (exactly 200Hz)
  if (currentMicros - lastSampleTime >= sampleIntervalMicros) {
    lastSampleTime = currentMicros;

    // 1. Non-blocking serial read to receive CPM and Triage from Python
    while (Serial.available() > 0) {
      char inChar = (char)Serial.read();
      if (inChar == '\n') {
        if (inputString.startsWith("C:")) {
          int displayVal = inputString.substring(2).toInt();
          display.showNumberDec(displayVal, false); 
        } else if (inputString.startsWith("T:")) {
          currentTriage = inputString.charAt(2);
        }
        inputString = "";
      } else {
        inputString += inChar;
      }
    }

    // 2. Hardware LED Flasher (500ms blink rate without blocking)
    if (currentMicros - lastBlinkTime >= 500000) {
      lastBlinkTime = currentMicros;
      ledState = !ledState;
      
      // Reset both LEDs before applying the active state
      digitalWrite(LED_GREEN_PIN, LOW);
      digitalWrite(LED_RED_PIN, LOW);

      if (ledState) {
        if (currentTriage == 'R') {
          digitalWrite(LED_RED_PIN, HIGH);
        } else if (currentTriage == 'G' || currentTriage == 'Y') {
          // Both Normal (Green) and Abnormal (Yellow) will pulse Green for now, 
          // or you could add a Yellow LED. Assuming Normal pulses Green.
          digitalWrite(LED_GREEN_PIN, HIGH);
        }
      }
    }

    // 2. Check if leads are disconnected
    bool leadsOff = (digitalRead(LO_PLUS_PIN) == 1) || (digitalRead(LO_MINUS_PIN) == 1);

    // 3. Read the analog value
    int sensorValue = analogRead(AD8232_PIN);

    // 4. Print value and leads-off
    Serial.print(sensorValue);
    Serial.print(",");
    Serial.println(leadsOff ? 1 : 0);
  }
}
