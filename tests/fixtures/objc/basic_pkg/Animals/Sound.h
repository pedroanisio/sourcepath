#import <Foundation/Foundation.h>

@interface Sound : NSObject
- (instancetype)initWithText:(NSString *)text;
- (NSString *)text;
- (Sound *)amplify;
@end
