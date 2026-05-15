#import "Sound.h"

@implementation Sound
{
    NSString *_text;
}

- (instancetype)initWithText:(NSString *)text {
    self = [super init];
    if (self) {
        _text = [text copy];
    }
    return self;
}

- (NSString *)text {
    return _text;
}

- (Sound *)amplify {
    NSString *louder = [NSString stringWithFormat:@"%@!", _text];
    return [[Sound alloc] initWithText:louder];
}
@end
