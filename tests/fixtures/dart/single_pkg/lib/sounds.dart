class Sound {
  final String text;
  Sound(this.text);

  void amplify() {
    // intentionally trivial — exists so the xref resolver can attach
    // a call edge to it from animals.dart's eat() method.
  }
}
